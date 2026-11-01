package scrimmage.agent;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * scrimmage's REV SPARK helper. Every SPARK the robot program creates is registered here (from a hook at the end of
 * SparkBase's constructor); once per robot loop, before simulationPeriodic, the helper drives REV's own SparkSim for
 * each SPARK with scrimmage's physics values, exchanged through a HAL SimDevice "ScrimmageREV:SparkMax[id]" /
 * "ScrimmageREV:SparkFlex[id]" per SPARK:
 *
 * <ul>
 *   <li>inputs (written by scrimmage): position (motor shaft rotations since the start, physical frame; NaN
 *       until scrimmage writes it), velocity (motor shaft rpm, physical frame), vbus (V), current (A),
 *       absRotations (absolute-encoder magnet rotations, unwrapped), fwdLimit, revLimit, motor (model code:
 *       1 NEO, 2 NEO 550, 3 NEO Vortex, else the SPARK's usual motor);
 *   <li>output (read by scrimmage): motorVoltage, the voltage across the physical motor (after the SPARK's
 *       inversion and follower mirroring).
 * </ul>
 *
 * REVLib and WPILib are reached by reflection only, so the agent builds without them and robots without REV pay
 * nothing: the helper starts on the first SPARK. If REVLib lacks something it uses, it prints one
 * "scrimmage agent: REV sim unavailable: <reason>" line and does nothing more.
 */
public final class ScrimmageRev {
  private ScrimmageRev() {}

  private static final ThreadLocal<Boolean> INSIDE = ThreadLocal.withInitial(() -> Boolean.FALSE);
  private static final List<Object> PENDING = new ArrayList<>();  // guarded by itself
  private static boolean started;                                   // guarded by PENDING
  private static volatile boolean unavailable;                      // written under PENDING
  private static Api api;
  private static Object callback;  // the SimPeriodicBefore registration, kept for the life of the program
  // robot thread only (the sim-periodic callback)
  private static final List<Spark> SPARKS = new ArrayList<>();
  private static double lastTime = Double.NaN;
  private static String lastFailure;

  /**
   * For the patched SparkSim.iterate: true while the helper itself is driving REV's simulation, and always once REV
   * is unavailable (the helper won't drive it, so the robot program's own SparkSim physics keeps working).
   */
  public static boolean inside() {
    return unavailable || INSIDE.get();
  }

  /** Called at the end of every SparkBase constructor (before each RETURN), with the new SPARK. */
  public static void register(Object spark) {
    try {
      synchronized (PENDING) {
        if (unavailable) return;
        PENDING.add(spark);
        if (started) return;
        started = true;
      }
      start(spark.getClass().getClassLoader());
    } catch (Throwable t) {
      fail("registering a SPARK", t);
    }
  }

  /** Resolve REVLib/WPILib and hook the robot loop; any failure makes REV unavailable, said once. */
  private static void start(ClassLoader loader) {
    try {
      api = new Api(loader);
      callback = api.registerCallback.invoke(null, (Runnable) ScrimmageRev::loop);
    } catch (Missing m) {
      markUnavailable(m.getMessage());
    } catch (Throwable t) {
      markUnavailable(describe(t));
    }
  }

  private static void markUnavailable(String reason) {
    synchronized (PENDING) {
      unavailable = true;
      PENDING.clear();
    }
    System.out.println("scrimmage agent: REV sim unavailable: " + reason);
  }

  // ------------------------------------------------------------------ the robot loop

  private static void loop() {
    INSIDE.set(Boolean.TRUE);
    try {
      double now = (double) api.fpgaTime.invoke(null);
      double dt = Double.isNaN(lastTime) ? 0.02 : now - lastTime;
      lastTime = now;
      addPending();
      Map<Integer, Spark> byId = new HashMap<>();
      for (Iterator<Spark> it = SPARKS.iterator(); it.hasNext(); ) {
        Spark s = it.next();
        try {
          s.prepare(dt);
          byId.putIfAbsent(s.id, s);
        } catch (Throwable t) {
          drop(s, t);
          it.remove();
        }
      }
      for (Iterator<Spark> it = SPARKS.iterator(); it.hasNext(); ) {
        Spark s = it.next();
        try {
          if (s.leaderId == 0) s.drive(dt);
        } catch (Throwable t) {
          drop(s, t);
          it.remove();
          byId.remove(s.id, s);
        }
      }
      for (Iterator<Spark> it = SPARKS.iterator(); it.hasNext(); ) {
        Spark s = it.next();
        try {
          if (s.leaderId != 0) s.follow(byId);
          s.publish();
        } catch (Throwable t) {
          drop(s, t);
          it.remove();
        }
      }
    } catch (Throwable t) {
      fail("the REV robot-loop step", t);
    } finally {
      INSIDE.set(Boolean.FALSE);
    }
  }

  private static void addPending() {
    List<Object> fresh;
    synchronized (PENDING) {
      if (PENDING.isEmpty()) return;
      fresh = new ArrayList<>(PENDING);
      PENDING.clear();
    }
    for (Object base : fresh) {
      try {
        Spark s = new Spark(base);
        if (s.device != null) SPARKS.add(s);
      } catch (Throwable t) {
        fail("setting up a SPARK", t);
      }
    }
  }

  private static void drop(Spark s, Throwable t) {
    System.out.println("scrimmage agent: " + s.label + " is no longer simulated: " + describe(t));
    try {
      api.simDeviceClose.invoke(s.device);
    } catch (Throwable ignored) {
      // closing is best effort
    }
  }

  private static void fail(String what, Throwable t) {
    String msg = describe(t);
    if (!msg.equals(lastFailure)) {  // once per distinct problem, not 50 times a second
      lastFailure = msg;
      System.out.println("scrimmage agent: REV helper failed in " + what + ": " + msg);
    }
  }

  private static String describe(Throwable t) {
    while (t instanceof InvocationTargetException && t.getCause() != null) t = t.getCause();
    return t.getClass().getSimpleName() + (t.getMessage() != null ? ": " + t.getMessage() : "");
  }

  private static double mod1(double x) {
    return x - Math.floor(x);
  }

  private static double factor(double f) {
    return f == 0.0 ? 1.0 : f;  // REV treats a zero conversion factor as 1
  }

  // ------------------------------------------------------------------ one SPARK

  private static final class Spark {
    final Object base;
    final boolean flex;
    final int id;
    final String label;   // "SPARK MAX 3"
    final Object config;  // its configAccessor
    final Object device;  // the ScrimmageREV helper SimDevice (null if it couldn't be made)
    Object position, velocity, vbus, current, absRotations, fwdLimit, revLimit, motor, motorVoltage;
    Object sim, absSim, fwdSim, revSim, relSim;
    Method motorModel;         // the DCMotor factory the SparkSim was built with
    int leaderId;              // 0: not a follower
    boolean inverted, followInverted, warnedLeader;
    double absPos, absVel, prevAbs = Double.NaN;
    double applied, physSign = 1.0;
    // the built-in encoder, in the robot's units: set from the motor's real rotation (not integrated), plus the
    // offset the robot program's own setPosition calls make
    double relPos = Double.NaN, relVel, relOffset = Double.NaN, lastRel = Double.NaN, encPosF = 1.0, encVelF = 1.0;
    boolean relFromIterate;

    Spark(Object base) throws Exception {
      this.base = base;
      flex = api.sparkFlex != null && api.sparkFlex.isInstance(base);
      id = (int) api.getDeviceId.invoke(base);
      label = (flex ? "SPARK Flex " : "SPARK MAX ") + id;
      config = (flex ? api.flexConfig : api.maxConfig).get(base);
      String name = "ScrimmageREV:" + (flex ? "SparkFlex[" : "SparkMax[") + id + "]";
      device = api.simCreate.invoke(null, name);
      if (device == null) {
        System.out.println("scrimmage agent: " + label + " is not simulated: can't create its scrimmage device "
            + name + " (two SPARKs with that id?)");
        return;
      }
      position = input("position", Double.NaN);
      velocity = input("velocity", 0.0);
      vbus = input("vbus", 12.0);
      current = input("current", 0.0);
      absRotations = input("absRotations", 0.0);
      motor = input("motor", 0.0);
      fwdLimit = api.createBoolean.invoke(device, "fwdLimit", api.kInput, false);
      revLimit = api.createBoolean.invoke(device, "revLimit", api.kInput, false);
      motorVoltage = api.createDouble.invoke(device, "motorVoltage", api.kOutput, 0.0);
    }

    private Object input(String name, double initial) throws Exception {
      return api.createDouble.invoke(device, name, api.kInput, initial);
    }

    private double get(Object simDouble) throws Exception {
      return (double) api.doubleGet.invoke(simDouble);
    }

    /** Read the config and inputs; (re)build the SparkSim; set the limit switches and absolute encoder. */
    void prepare(double dt) throws Exception {
      leaderId = (int) api.leaderId.invoke(config);
      followInverted = (boolean) api.followerInverted.invoke(config);
      inverted = (boolean) api.inverted.invoke(config);
      int code = (int) Math.round(get(motor));
      Method model = code == 1 ? api.neo : code == 2 ? api.neo550 : code == 3 ? api.vortex
          : flex ? api.vortex : api.neo;
      if (model != motorModel) {  // only a different motor: a new SparkSim starts REV's filters over
        sim = (flex ? api.flexSim : api.maxSim).newInstance(base, model.invoke(null, 1));
        motorModel = model;
        absSim = api.getAbsSim.invoke(sim);
        fwdSim = api.getFwdSim.invoke(sim);
        revSim = api.getRevSim.invoke(sim);
        relSim = api.getRelSim.invoke(sim);
      }
      api.setPressed.invoke(fwdSim, (boolean) api.booleanGet.invoke(fwdLimit));
      api.setPressed.invoke(revSim, (boolean) api.booleanGet.invoke(revLimit));

      Object abs = api.absAccessor.get(config);
      boolean absInverted = (boolean) api.absInverted.invoke(abs);
      double r = get(absRotations);
      // REVLib: the zero offset is the reading at the zero position as if not inverted (and factor 1)
      double fromZero = r - (double) api.absZeroOffset.invoke(abs);
      absPos = mod1(absInverted ? -fromZero : fromZero) * factor((double) api.absPositionFactor.invoke(abs));
      if (dt > 0) {
        double rpm = Double.isNaN(prevAbs) ? 0.0 : (r - prevAbs) / dt * 60.0 * (absInverted ? -1.0 : 1.0);
        absVel = rpm * factor((double) api.absVelocityFactor.invoke(abs));
        prevAbs = r;
      }
      api.absSetPosition.invoke(absSim, absPos);
      api.absSetVelocity.invoke(absSim, absVel);

      // the built-in encoder counts the motor's rotation in the SPARK's frame (a follower: its own inverted setting)
      Object enc = api.encoderAccessor.get(config);
      encPosF = factor((double) api.encPositionFactor.invoke(enc));
      encVelF = factor((double) api.encVelocityFactor.invoke(enc));
      double sign = inverted ? -1.0 : 1.0;
      double rot = get(position);
      relVel = get(velocity) * sign * encVelF;
      relFromIterate = false;
      double cur = (double) api.relGetPosition.invoke(relSim);
      if (Double.isNaN(relOffset)) {
        relOffset = cur;                     // counts from the start (power-on), keeping a value the robot set
      } else if (!Double.isNaN(lastRel) && cur != lastRel) {
        relOffset += cur - lastRel;          // the robot program set the encoder's position
      }
      if (Double.isNaN(rot)) {  // scrimmage hasn't sent it (yet, or the SPARK is unbound): leave the encoder to REV
        relPos = Double.NaN;
        return;
      }
      relPos = rot * sign * encPosF + relOffset;
    }

    /** A leader (or a SPARK on its own): run REV's simulated controller for dt. */
    void drive(double dt) throws Exception {
      physSign = inverted ? -1.0 : 1.0;
      if (dt > 0) {
        Object sensor = api.feedbackSensor.invoke(api.closedLoopAccessor.get(config));
        boolean onAbs = sensor instanceof Enum && "kAbsoluteEncoder".equals(((Enum<?>) sensor).name());
        boolean onPrimary = sensor instanceof Enum && "kPrimaryEncoder".equals(((Enum<?>) sensor).name());
        // iterate integrates its position from the velocity: start it so that it lands on the real position, which
        // its closed loop then uses (an integral of sampled velocities drifts, e.g. against a hard stop)
        double v;
        if (onAbs) {
          Object abs = api.absAccessor.get(config);
          double absPosF = factor((double) api.absPositionFactor.invoke(abs));
          double absVelF = factor((double) api.absVelocityFactor.invoke(abs));
          v = absVel;
          api.setPosition.invoke(sim, absPos - v / absVelF / 60.0 * dt * absPosF);
        } else {
          v = get(velocity) * physSign * encVelF;
          if (onPrimary && !Double.isNaN(relPos)) {
            api.setPosition.invoke(sim, relPos - v / encVelF / 60.0 * dt * encPosF);
            relFromIterate = true;  // iterate sets the encoder's (filtered, noisy) velocity
          }
        }
        api.iterate.invoke(sim, v, Math.max(get(vbus), 0.1), dt);
        if (onAbs) {  // iterate mirrors its integrated position into the absolute encoder: put the real one back
          api.absSetPosition.invoke(absSim, absPos);
          api.absSetVelocity.invoke(absSim, absVel);
        }
      }
      applied = (double) api.getApplied.invoke(sim);
    }

    /** A follower: mirror its leader's output (negated if it follows inverted). */
    void follow(Map<Integer, Spark> byId) throws Exception {
      Spark top = this;
      double sign = 1.0;
      for (int depth = 0; top != null && top.leaderId != 0 && depth < 8; depth++) {
        sign *= top.followInverted ? -1.0 : 1.0;
        Spark next = byId.get(top.leaderId);
        if (next == null || next == top) {
          if (!warnedLeader) {
            warnedLeader = true;
            System.out.println("scrimmage agent: " + label + " follows CAN id " + top.leaderId
                + ", but the robot program has no SPARK with that id; its output stays 0");
          }
          top = null;
          break;
        }
        top = next;
      }
      if (top == null || top.leaderId != 0) {  // missing leader, or a follower loop
        applied = 0.0;
        physSign = 1.0;
      } else {
        applied = top.applied * sign;
        physSign = top.physSign;  // a follower mirrors its leader's physical output
      }
      api.setApplied.invoke(sim, applied);
      api.setBusVoltage.invoke(sim, Math.max(get(vbus), 0.1));
    }

    /** The physics current into REV, and the motor voltage out to scrimmage. */
    void publish() throws Exception {
      if (!Double.isNaN(relPos)) {
        api.relSetPosition.invoke(relSim, relPos);
        if (!relFromIterate) api.relSetVelocity.invoke(relSim, relVel);
      }
      lastRel = (double) api.relGetPosition.invoke(relSim);
      api.setCurrent.invoke(sim, get(current));
      api.doubleSet.invoke(motorVoltage, applied * get(vbus) * physSign);
    }
  }

  // ------------------------------------------------------------------ REVLib / WPILib by reflection

  private static final class Missing extends Exception {
    Missing(String what) {
      super(what);
    }
  }

  private static final class Api {
    final Class<?> sparkFlex;
    final Field maxConfig, flexConfig;
    final Method getDeviceId;
    final Method inverted, leaderId, followerInverted;
    final Field encoderAccessor, absAccessor, closedLoopAccessor;
    final Method encPositionFactor, encVelocityFactor, absInverted, absZeroOffset, absPositionFactor, absVelocityFactor;
    final Method feedbackSensor;
    final Constructor<?> maxSim, flexSim;
    final Method iterate, setApplied, getApplied, setCurrent, setBusVoltage, setPosition;
    final Method getAbsSim, getFwdSim, getRevSim, absSetPosition, absSetVelocity, setPressed;
    final Method getRelSim, relGetPosition, relSetPosition, relSetVelocity;
    final Method neo, neo550, vortex;
    final Method simCreate, createDouble, createBoolean, simDeviceClose, doubleGet, doubleSet, booleanGet;
    final Object kInput, kOutput;
    final Method fpgaTime, registerCallback;
    private final ClassLoader loader;

    Api(ClassLoader loader) throws Missing {
      this.loader = loader;
      Class<?> base = cls("com.revrobotics.spark.SparkBase");
      Class<?> max = cls("com.revrobotics.spark.SparkMax");
      sparkFlex = cls("com.revrobotics.spark.SparkFlex");
      maxConfig = field(max, "configAccessor");
      flexConfig = field(sparkFlex, "configAccessor");
      getDeviceId = method(base, "getDeviceId");
      Class<?> acc = cls("com.revrobotics.spark.config.SparkBaseConfigAccessor");
      inverted = method(acc, "getInverted");
      leaderId = method(acc, "getFollowerModeLeaderId");
      followerInverted = method(acc, "getFollowerModeInverted");
      encoderAccessor = field(acc, "encoder");
      absAccessor = field(acc, "absoluteEncoder");
      closedLoopAccessor = field(acc, "closedLoop");
      Class<?> encAcc = cls("com.revrobotics.spark.config.EncoderConfigAccessor");
      encPositionFactor = method(encAcc, "getPositionConversionFactor");
      encVelocityFactor = method(encAcc, "getVelocityConversionFactor");
      Class<?> abs = cls("com.revrobotics.spark.config.AbsoluteEncoderConfigAccessor");
      absInverted = method(abs, "getInverted");
      absZeroOffset = method(abs, "getZeroOffset");
      absPositionFactor = method(abs, "getPositionConversionFactor");
      absVelocityFactor = method(abs, "getVelocityConversionFactor");
      feedbackSensor = method(cls("com.revrobotics.spark.config.ClosedLoopConfigAccessor"), "getFeedbackSensor");
      Class<?> dcMotor = cls("edu.wpi.first.math.system.plant.DCMotor");
      maxSim = constructor(cls("com.revrobotics.sim.SparkMaxSim"), max, dcMotor);
      flexSim = constructor(cls("com.revrobotics.sim.SparkFlexSim"), sparkFlex, dcMotor);
      Class<?> sim = cls("com.revrobotics.spark.SparkSim");
      iterate = method(sim, "iterate", double.class, double.class, double.class);
      setApplied = method(sim, "setAppliedOutput", double.class);
      getApplied = method(sim, "getAppliedOutput");
      setCurrent = method(sim, "setMotorCurrent", double.class);
      setBusVoltage = method(sim, "setBusVoltage", double.class);
      setPosition = method(sim, "setPosition", double.class);
      getAbsSim = method(sim, "getAbsoluteEncoderSim");
      getFwdSim = method(sim, "getForwardLimitSwitchSim");
      getRevSim = method(sim, "getReverseLimitSwitchSim");
      Class<?> absSim = cls("com.revrobotics.sim.SparkAbsoluteEncoderSim");
      absSetPosition = method(absSim, "setPosition", double.class);
      absSetVelocity = method(absSim, "setVelocity", double.class);
      setPressed = method(cls("com.revrobotics.sim.SparkLimitSwitchSim"), "setPressed", boolean.class);
      getRelSim = method(sim, "getRelativeEncoderSim");
      Class<?> relSim = cls("com.revrobotics.sim.SparkRelativeEncoderSim");
      relGetPosition = method(relSim, "getPosition");
      relSetPosition = method(relSim, "setPosition", double.class);
      relSetVelocity = method(relSim, "setVelocity", double.class);
      neo = method(dcMotor, "getNEO", int.class);
      neo550 = method(dcMotor, "getNeo550", int.class);
      vortex = method(dcMotor, "getNeoVortex", int.class);
      Class<?> simDevice = cls("edu.wpi.first.hal.SimDevice");
      Class<?> direction = cls("edu.wpi.first.hal.SimDevice$Direction");
      simCreate = method(simDevice, "create", String.class);
      createDouble = method(simDevice, "createDouble", String.class, direction, double.class);
      createBoolean = method(simDevice, "createBoolean", String.class, direction, boolean.class);
      simDeviceClose = method(simDevice, "close");
      kInput = constant(direction, "kInput");
      kOutput = constant(direction, "kOutput");
      Class<?> simDouble = cls("edu.wpi.first.hal.SimDouble");
      doubleGet = method(simDouble, "get");
      doubleSet = method(simDouble, "set", double.class);
      booleanGet = method(cls("edu.wpi.first.hal.SimBoolean"), "get");
      fpgaTime = method(cls("edu.wpi.first.wpilibj.Timer"), "getFPGATimestamp");
      registerCallback = method(cls("edu.wpi.first.hal.HAL"), "registerSimPeriodicBeforeCallback", Runnable.class);
    }

    private Class<?> cls(String name) throws Missing {
      try {
        return Class.forName(name, false, loader);
      } catch (ClassNotFoundException | LinkageError e) {
        throw new Missing("no class " + name);
      }
    }

    private static Method method(Class<?> c, String name, Class<?>... params) throws Missing {
      try {
        return c.getMethod(name, params);
      } catch (NoSuchMethodException | LinkageError e) {
        throw new Missing("no method " + c.getName() + "." + name + signature(params));
      }
    }

    private static Constructor<?> constructor(Class<?> c, Class<?>... params) throws Missing {
      try {
        return c.getConstructor(params);
      } catch (NoSuchMethodException | LinkageError e) {
        throw new Missing("no constructor " + c.getName() + signature(params));
      }
    }

    private static Field field(Class<?> c, String name) throws Missing {
      try {
        return c.getField(name);
      } catch (NoSuchFieldException | LinkageError e) {
        throw new Missing("no field " + c.getName() + "." + name);
      }
    }

    private static Object constant(Class<?> c, String name) throws Missing {
      try {
        return c.getField(name).get(null);
      } catch (ReflectiveOperationException | LinkageError e) {
        throw new Missing("no constant " + c.getName() + "." + name);
      }
    }

    private static String signature(Class<?>[] params) {
      StringBuilder sb = new StringBuilder("(");
      for (int i = 0; i < params.length; i++) {
        if (i > 0) sb.append(", ");
        sb.append(params[i].getSimpleName());
      }
      return sb.append(")").toString();
    }
  }
}
