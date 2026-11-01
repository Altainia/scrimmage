package scrimmage.sim;

import edu.wpi.first.hal.HALUtil;
import edu.wpi.first.hal.SimBoolean;
import edu.wpi.first.hal.SimDevice;
import edu.wpi.first.hal.SimDevice.Direction;
import edu.wpi.first.hal.SimDouble;
import edu.wpi.first.hal.simulation.DriverStationDataJNI;
import edu.wpi.first.wpilibj.Notifier;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.LongSupplier;

/**
 * A simulated Kraken X60 with Talon-style control ({@link TalonControl}), the HAL SimDevice
 * {@code ScrimmageMotor:<name>}.
 *
 * <p>The protocol scrimmage relies on. Outputs: {@code motorVoltage} (V across the physical motor, after
 * inversion) and {@code brake}. Inputs, written by scrimmage: {@code position} (rotor rotations since the start,
 * physical frame: counter-clockwise positive, as CTRE's CounterClockwise_Positive), {@code velocity} (rotor rps,
 * same frame) and {@code vbus} (V; 12 until scrimmage writes it).
 *
 * <p>Units follow CTRE's documented meaning of the ratios: the sensor is the rotor (or, after
 * {@link #useRemoteEncoder}, the CANcoder), {@code sensorToMechanismRatio} is sensor rotations per mechanism
 * rotation and {@code rotorToSensorRatio} is rotor rotations per CANcoder rotation. With a remote CANcoder the
 * position comes from the CANcoder and the velocity from the rotor (as a fused CANcoder does), scaled by both
 * ratios. Inversion ({@code inverted} = clockwise positive) flips the voltage applied and the readings.
 *
 * <p>Control runs on one shared HAL {@link Notifier} every 5 ms (the real Talon runs at 1 kHz), on the robot
 * program's simulated clock, updating leaders before their followers. While the robot is disabled the output is
 * neutral, as a Talon's is.
 *
 * <p>Timing is deterministic by construction. The sim HAL wakes every notifier that comes due at once, on separate
 * threads, so the robot loop and a control tick can run at the same simulated time in either order. Robot-thread
 * calls ({@link #setControl}, {@link #follow}, {@link #useRemoteEncoder}, {@link #setPosition}) are stamped with
 * the HAL time: a tick at time {@code t} uses only what was handed over before {@code t}, so a request made at
 * the tick's own time takes effect at the next tick (at most 5 ms later). Likewise the robot thread at time
 * {@code t} reads the results ({@link #getMotorVoltage} and the currents) of the last tick before {@code t},
 * never of a tick at {@code t}.
 *
 * <p>Where scrimmage isn't running (a RoboRIO, a plain test) the HAL gives no SimDevice: the device then does
 * nothing, starts no notifier and reads 0.
 */
public class ScrimmageKraken implements AutoCloseable {
    static final String TYPE = "ScrimmageMotor";
    static final double PERIOD = 0.005;
    private static final double NOMINAL_VBUS = 12.0;
    private static final int MAX_FOLLOW_CHAIN = 64; // a guard: follow() refuses loops

    /** The HAL's clock (µs), frozen while scrimmage steps the robot program. */
    static final LongSupplier HAL_CLOCK = ScrimmageKraken::fpgaMicros;
    /** The clock that stamps requests and results; tests replace it. */
    static volatile LongSupplier clock = HAL_CLOCK;

    private static final List<ScrimmageKraken> ALL = new ArrayList<>(); // guarded by itself
    private static final Object FOLLOWING = new Object(); // held by follow(): see there
    private static Notifier notifier; // guarded by ALL
    private static long lastTickMicros = -1; // notifier thread only

    private final String name;
    private final KrakenSettings settings;
    private final TalonControl control;
    private final SimDevice device;
    private final SimDouble motorVoltageOut;
    private final SimBoolean brakeOut;
    private final SimDouble positionIn;
    private final SimDouble velocityIn;
    private final SimDouble vbusIn;

    /** What the robot thread hands the control loop. */
    private record Command(ControlRequest request, ScrimmageKraken leader, boolean opposed, ScrimmageCANcoder remote,
            boolean fused, double positionOffset) {
        Command withRequest(ControlRequest r) {
            return new Command(r, null, opposed, remote, fused, positionOffset);
        }

        Command withLeader(ScrimmageKraken l, boolean o) {
            return new Command(request, l, o, remote, fused, positionOffset);
        }

        Command withRemote(ScrimmageCANcoder e, boolean f) {
            return new Command(request, leader, opposed, e, f, positionOffset);
        }

        Command withOffset(double offset) {
            return new Command(request, leader, opposed, remote, fused, offset);
        }
    }

    private record Stamped(long micros, Command command) {}

    /** One tick's results, stamped with the tick's time. */
    private record Output(long micros, double volts, double stator, double supply) {
        static final Output NONE = new Output(Long.MIN_VALUE, 0, 0, 0);
    }

    // Guarded by this. The robot thread's view: what it last asked for, read back by its own getters at once.
    private Command latest = new Command(ControlRequest.neutral(), null, false, null, false, 0);
    // Guarded by this. Commands not yet taken by a tick, oldest first, at most one per timestamp.
    private final ArrayDeque<Stamped> pending = new ArrayDeque<>();
    // Guarded by this. The control loop's view: the newest command stamped before the current tick.
    private Command active = latest;
    // Guarded by this. The last two ticks' results.
    private Output current = Output.NONE;
    private Output previous = Output.NONE;
    // Guarded by this. Set by close(): later commands are ignored.
    private boolean closed;

    /**
     * @param settings copied here: changing the object later (or building another device from it) doesn't affect
     *     this device, whose settings the control loop reads on its own thread
     */
    public ScrimmageKraken(String name, KrakenSettings settings) {
        this.settings = settings.copy(); // before the name is claimed, so a bad argument leaves it free
        this.control = new TalonControl(this.settings);
        Scrimmage.claim(TYPE, name);
        this.name = name;
        device = Scrimmage.createSimDevice(TYPE, name);
        if (device == null) {
            motorVoltageOut = null;
            brakeOut = null;
            positionIn = velocityIn = vbusIn = null;
            return;
        }
        motorVoltageOut = device.createDouble("motorVoltage", Direction.kOutput, 0);
        brakeOut = device.createBoolean("brake", Direction.kOutput, settings.brake);
        positionIn = device.createDouble("position", Direction.kInput, 0);
        velocityIn = device.createDouble("velocity", Direction.kInput, 0);
        vbusIn = device.createDouble("vbus", Direction.kInput, NOMINAL_VBUS);
        synchronized (ALL) {
            ALL.add(this);
            if (notifier == null) {
                notifier = new Notifier(ScrimmageKraken::tick);
                notifier.setName("ScrimmageKraken");
                notifier.startPeriodic(PERIOD);
            }
        }
    }

    // ---- commands (robot thread) ----

    /** Runs {@code r} (mechanism units) from the next control tick. Stops following. */
    public synchronized void setControl(ControlRequest r) {
        hand(latest.withRequest(r));
    }

    /**
     * Follows {@code leader}'s output. As CTRE documents for its Follower request, the follower then turns the same
     * physical way as the leader ({@code opposed} false) or the opposite way, whatever its own {@code inverted}.
     */
    public void follow(ScrimmageKraken leader, boolean opposed) {
        // One follow() at a time, so two threads can't each pass the check and make a loop between them. The walk
        // takes one device lock at a time, and FOLLOWING is never taken under a device lock.
        synchronized (FOLLOWING) {
            for (ScrimmageKraken k = leader; k != null; k = k.leader()) {
                if (k == this) {
                    throw new IllegalArgumentException("\"" + name + "\" can't follow \"" + leader.name
                            + "\": that would make a follow loop");
                }
            }
            synchronized (this) {
                hand(latest.withLeader(leader, opposed));
            }
        }
    }

    /** Closes the loop on {@code encoder}'s position as a fused CANcoder: {@code useRemoteEncoder(encoder, true)}. */
    public void useRemoteEncoder(ScrimmageCANcoder encoder) {
        useRemoteEncoder(encoder, true);
    }

    /**
     * Closes the loop on {@code encoder}. As CTRE documents the feedback sources: fused ({@code FusedCANcoder}) takes
     * position from the CANcoder and velocity from the rotor; not fused ({@code RemoteCANcoder}) takes both from the
     * CANcoder, the rotor unused.
     */
    public synchronized void useRemoteEncoder(ScrimmageCANcoder encoder, boolean fused) {
        hand(latest.withRemote(encoder, fused));
    }

    /** Makes the current position read {@code mechRot} (mechanism rotations). */
    public void setPosition(double mechRot) {
        double sign = sign(); // walks the leaders one lock at a time, so not under this device's lock
        synchronized (this) {
            hand(latest.withOffset(mechRot - rawMechPosition(latest, sign)));
        }
    }

    /** Records {@code c} for the robot thread now and for the first control tick after now. */
    private void hand(Command c) {
        if (closed) {
            return; // closed: nothing reads it any more
        }
        latest = c;
        if (device == null) {
            return; // no control loop to hand it to
        }
        long now = clock.getAsLong();
        Stamped last = pending.peekLast();
        if (last != null && last.micros() == now) {
            pending.pollLast(); // a later call at the same time replaces it
        }
        pending.addLast(new Stamped(now, c));
    }

    // ---- readings (mechanism units, in the configured direction) ----

    public double getPosition() {
        double sign = sign();
        synchronized (this) {
            return rawMechPosition(latest, sign) + latest.positionOffset();
        }
    }

    public double getVelocity() {
        double sign = sign();
        synchronized (this) {
            return mechVelocity(latest, sign);
        }
    }

    /** The voltage applied to the motor, in the configured direction, by the last control tick before now. */
    public double getMotorVoltage() {
        return visibleOutput().volts();
    }

    /** Stator current (A) after the last control tick before now. */
    public double getStatorCurrent() {
        return visibleOutput().stator();
    }

    /** Supply current (A) after the last control tick before now. */
    public double getSupplyCurrent() {
        return visibleOutput().supply();
    }

    /** Commands waiting for a control tick (tests). */
    synchronized int pendingCount() {
        return pending.size();
    }

    private synchronized Output visibleOutput() {
        if (device == null) {
            return Output.NONE;
        }
        long now = clock.getAsLong();
        if (current.micros() < now) {
            return current;
        }
        return previous.micros() < now ? previous : Output.NONE;
    }

    @Override
    public void close() {
        synchronized (ALL) {
            ALL.remove(this);
        }
        synchronized (this) {
            closed = true;
            pending.clear();
        }
        if (device != null) {
            device.close();
        }
        Scrimmage.release(TYPE, name);
    }

    // ---- the control loop ----

    private static void tick() {
        long now = clock.getAsLong();
        double dt = lastTickMicros < 0 || now <= lastTickMicros ? PERIOD : (now - lastTickMicros) / 1e6;
        lastTickMicros = now;
        updateAll(now, dt);
    }

    /** Updates every Kraken with a SimDevice as a tick at time {@code nowMicros}, leaders before followers. */
    static void updateAll(long nowMicros, double dt) {
        List<ScrimmageKraken> krakens;
        synchronized (ALL) {
            krakens = new ArrayList<>(ALL);
        }
        for (ScrimmageKraken k : krakens) {
            k.takeCommandsBefore(nowMicros);
        }
        int limit = krakens.size();
        krakens.sort(Comparator.comparingInt(k -> k.followDepth(limit)));
        boolean enabled = robotEnabled();
        for (ScrimmageKraken k : krakens) {
            k.update(nowMicros, dt, enabled);
        }
    }

    /**
     * Enabled as scrimmage set it. This is the HAL's simulated driver-station state, not the robot thread's
     * refreshed copy: that changes whenever the robot loop refreshes, which may be during a tick at the same time.
     */
    private static boolean robotEnabled() {
        try {
            return DriverStationDataJNI.getEnabled();
        } catch (LinkageError e) {
            return false;
        }
    }

    private synchronized void takeCommandsBefore(long nowMicros) {
        while (!pending.isEmpty() && pending.peekFirst().micros() < nowMicros) {
            active = pending.pollFirst().command();
        }
    }

    private void update(long nowMicros, double dt, boolean enabled) {
        Command c;
        double mechPosition;
        double mechVelocity;
        double sign = activeSign(); // not under this device's lock: see sign()
        synchronized (this) {
            c = active;
            mechPosition = rawMechPosition(c, sign) + c.positionOffset();
            mechVelocity = mechVelocity(c, sign);
        }
        double motorRps = sign * input(velocityIn, 0);
        double vbus = input(vbusIn, NOMINAL_VBUS);
        double volts;
        double stator;
        double supply;
        if (!enabled) {
            control.reset();
            volts = 0;
            stator = settings.brake ? KrakenModel.statorCurrent(0, motorRps) : 0;
            supply = 0;
        } else if (c.leader() != null) {
            // The follower's own direction is the leader's (flipped when opposed), so the same applied voltage
            // turns it the right physical way. Leaders update first, so this is the leader's output this tick.
            control.reset();
            volts = c.leader().tickVolts();
            stator = KrakenModel.statorCurrent(volts, motorRps);
            supply = vbus > 0 ? stator * Math.abs(volts) / vbus : 0;
        } else {
            volts = control.update(c.request(), mechPosition, mechVelocity, motorRps, vbus, dt);
            stator = control.statorCurrent();
            supply = control.supplyCurrent();
        }
        synchronized (this) {
            previous = current;
            current = new Output(nowMicros, volts, stator, supply);
        }
        if (motorVoltageOut != null) {
            motorVoltageOut.set(sign * volts);
            brakeOut.set(settings.brake);
        }
    }

    /** The newest tick's applied voltage, for followers in the same tick; 0 (neutral) once closed. */
    private synchronized double tickVolts() {
        return closed ? 0 : current.volts();
    }

    // ---- helpers ----

    /** The leader per the robot thread's latest command, or null (package-private for tests). */
    synchronized ScrimmageKraken leader() {
        return latest.leader();
    }

    private synchronized ScrimmageKraken activeLeader() {
        return active.leader();
    }

    /** How many leaders the control loop's command puts above this device (capped, in case of a loop). */
    private int followDepth(int limit) {
        int depth = 0;
        for (ScrimmageKraken k = activeLeader(); k != null && depth < limit; k = k.activeLeader()) {
            depth++;
        }
        return depth;
    }

    /**
     * -1 when the motor's positive direction is clockwise: its own setting, or its leader's when following.
     *
     * <p>Walks up the leaders taking one device's lock at a time, never two at once, and must be called holding
     * none. The robot thread walks the {@code latest} follow graph and the control loop the {@code active} one;
     * between a call and the tick that takes it the two can disagree (a leader and follower swapping roles), so
     * nested locking along them could deadlock.
     */
    private double sign() {
        return walkSign(false);
    }

    /** {@link #sign()} as the control loop's command has it. */
    private double activeSign() {
        return walkSign(true);
    }

    private double walkSign(boolean useActive) {
        double sign = 1;
        ScrimmageKraken k = this;
        for (int hops = 0; ; hops++) {
            Command c;
            synchronized (k) {
                c = useActive ? k.active : k.latest;
            }
            if (c.leader() == null || hops > MAX_FOLLOW_CHAIN) {
                return sign * (k.settings.inverted ? -1 : 1);
            }
            sign *= c.opposed() ? -1 : 1;
            k = c.leader();
        }
    }

    private double rawMechPosition(Command c, double sign) {
        if (c.remote() != null) {
            return c.remote().getPosition() / settings.sensorToMechanismRatio;
        }
        return sign * input(positionIn, 0) / settings.sensorToMechanismRatio;
    }

    private double mechVelocity(Command c, double sign) {
        if (c.remote() != null && !c.fused()) {
            return c.remote().getVelocity() / settings.sensorToMechanismRatio;
        }
        double rotor = sign * input(velocityIn, 0);
        return c.remote() != null
                ? rotor / (settings.rotorToSensorRatio * settings.sensorToMechanismRatio)
                : rotor / settings.sensorToMechanismRatio;
    }

    private static long fpgaMicros() {
        try {
            return HALUtil.getFPGATime();
        } catch (LinkageError e) {
            return 0;
        }
    }

    private static double input(SimDouble value, double fallback) {
        return value == null ? fallback : value.get();
    }
}
