package scrimmage.agent;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * scrimmage's Grapple LaserCAN helper. A LaserCAN registers nothing in simulation (its getMeasurement() gives null
 * with no CAN frames), so every LaserCan the robot program creates is registered here (from a hook at the end of its
 * constructor, with the CAN id the constructor got) and gets a HAL SimDevice "ScrimmageGrapple:LaserCAN[id]" whose
 * inputs scrimmage writes:
 *
 * <ul>
 *   <li>distance_mm: the distance to what the sensor sees, in mm, raw (not capped by the ranging mode);
 *   <li>status: {@link #STATUS_VALID} when it sees something, {@link #STATUS_NO_TARGET} when not.
 * </ul>
 *
 * The hooked getMeasurement() returns {@link #measurement} once scrimmage has written both (null, the real
 * class's answer with no frames, until then). The ranging mode, timing budget and region of interest robot code
 * sets are recorded and mirrored in the Measurement; the LaserCAN's own setters (which wait 800 ms for a CAN reply
 * in simulation and then throw ConfigurationFailedException) are skipped for a LaserCAN the helper feeds, as a real
 * one would accept them.
 *
 * <p>libgrapplefrc and WPILib are reached by reflection only, so the agent builds without them and robots without
 * libgrapplefrc pay nothing: the helper starts on the first LaserCAN and prints "scrimmage grapple helper: loaded".
 * If something it uses is missing or fails, it prints one "scrimmage grapple helper: unavailable: <reason>" line
 * and leaves every LaserCAN to its original behavior.
 */
public final class ScrimmageGrapple {
  private ScrimmageGrapple() {}

  // The status values scrimmage writes: libgrapplefrc's LaserCanInterface.LASERCAN_STATUS_VALID_MEASUREMENT (0) and
  // LASERCAN_STATUS_WEAK_SIGNAL (2), the no-target status chosen in
  // docs/superpowers/specs/2026-11-13-vendor-distance-sensors-checks.md (not seen on hardware). They mirror
  // scrimmage/sensors/runtime.py's LASERCAN_STATUS_VALID and LASERCAN_STATUS_NO_TARGET (a test pins them equal).
  static final int STATUS_VALID = 0;
  static final int STATUS_NO_TARGET = 2;
  // The ranging modes' reaches (mm): libgrapplefrc's RangingMode javadoc, "up to 4m" (LONG) and "1.3m and lower"
  // (SHORT) (GrappleRobotics/libgrapplefrc LaserCanInterface.java at 36fd307). Past its mode's reach a LaserCAN
  // reads no target, with distance_mm the reach.
  static final int LONG_REACH_MM = 4000;
  static final int SHORT_REACH_MM = 1300;
  // The firmware's defaults (GrappleRobotics/LaserCAN lasercan-impl/src/lib.rs lines 39-41): short ranging, ROI
  // x 8, y 8, w 16, h 16, a 33 ms timing budget. A unit set otherwise only in GrappleHook isn't seen here.
  static final int DEFAULT_BUDGET_MS = 33;
  private static final int[] DEFAULT_ROI = {8, 8, 16, 16};
  private static final int UNWRITTEN = Integer.MIN_VALUE;  // an input scrimmage hasn't written yet
  private static final long NO_READING = Long.MIN_VALUE;   // a Sensor's reading before scrimmage wrote both inputs

  private static final Map<Object, Sensor> SENSORS = new ConcurrentHashMap<>();  // by LaserCan (identity: no equals)
  private static final List<Sensor> PENDING = new ArrayList<>();  // guarded by itself
  private static boolean started;                                 // guarded by PENDING
  private static volatile boolean unavailable;                    // written under PENDING
  private static volatile Api api;  // set once the helper has started
  private static Object callback;  // the SimPeriodicBefore registration, kept for the life of the program
  private static final List<Sensor> LIVE = new ArrayList<>();  // robot thread only (the sim-periodic callback)

  /** Called at the end of every LaserCan(int) constructor (before each RETURN), with the new LaserCAN and its id. */
  public static void register(Object laserCan, int id) {
    try {
      Sensor s = new Sensor(id);
      synchronized (PENDING) {
        if (unavailable) return;
        SENSORS.put(laserCan, s);
        PENDING.add(s);
        if (started) return;
        started = true;
      }
      start(laserCan.getClass().getClassLoader());
    } catch (Throwable t) {
      markUnavailable("registering a LaserCAN: " + describe(t));
    }
  }

  /** At getMeasurement()'s entry: scrimmage's Measurement, or null to run the original (null until written). */
  public static Object measurement(Object laserCan) {
    try {
      Sensor s = fed(laserCan);
      return s == null ? null : s.measurement();
    } catch (Throwable t) {
      markUnavailable("reading a LaserCAN: " + describe(t));
      return null;
    }
  }

  /** At setRangingMode(RangingMode)'s entry: record the mode; true skips the original (it'd time out in sim). */
  public static boolean rangingMode(Object laserCan, Object mode) {
    Sensor s = fed(laserCan);
    if (s == null) return false;
    // as the original: LONG is long, anything else (null included) short
    s.isLong = mode instanceof Enum && "LONG".equals(((Enum<?>) mode).name());
    return true;
  }

  /** At setTimingBudget(TimingBudget)'s entry: record the budget; true skips the original. */
  public static boolean timingBudget(Object laserCan, Object budget) {
    Sensor s = fed(laserCan);
    if (s == null) return false;
    try {
      if (budget != null) s.budgetMs = (int) api.asMilliseconds.invoke(budget);
      return true;
    } catch (Throwable t) {
      markUnavailable("setting a LaserCAN's timing budget: " + describe(t));
      return false;
    }
  }

  /** At setRegionOfInterest(RegionOfInterest)'s entry: record a copy of the ROI; true skips the original. */
  public static boolean regionOfInterest(Object laserCan, Object roi) {
    Sensor s = fed(laserCan);
    if (s == null || roi == null) return false;  // a null ROI: the original's NullPointerException, as without us
    try {
      s.roi = new int[] {api.roiX.getInt(roi), api.roiY.getInt(roi), api.roiW.getInt(roi), api.roiH.getInt(roi)};
      return true;
    } catch (Throwable t) {
      markUnavailable("setting a LaserCAN's region of interest: " + describe(t));
      return false;
    }
  }

  private static Sensor fed(Object laserCan) {
    return unavailable || api == null ? null : SENSORS.get(laserCan);
  }

  /** Resolve libgrapplefrc/WPILib and hook the robot loop; any failure makes the helper unavailable, said once. */
  private static void start(ClassLoader loader) {
    try {
      Api a = new Api(loader);
      api = a;    // before the callback is registered, so its first call finds the API
      callback = a.registerCallback.invoke(null, (Runnable) ScrimmageGrapple::loop);
      System.out.println("scrimmage grapple helper: loaded");
    } catch (Missing m) {
      markUnavailable(m.getMessage());
    } catch (Throwable t) {
      markUnavailable(describe(t));
    }
  }

  private static void markUnavailable(String reason) {
    synchronized (PENDING) {
      if (unavailable) return;
      unavailable = true;
      PENDING.clear();
      SENSORS.clear();
    }
    System.out.println("scrimmage grapple helper: unavailable: " + reason);
  }

  // ------------------------------------------------------------------ the robot loop

  /** Once per robot loop, before simulationPeriodic: make new LaserCANs' devices, then take scrimmage's values. */
  private static void loop() {
    if (unavailable) return;
    try {
      List<Sensor> fresh;
      synchronized (PENDING) {
        fresh = new ArrayList<>(PENDING);
        PENDING.clear();
      }
      for (Sensor s : fresh) {
        if (s.open()) LIVE.add(s);
      }
      for (Sensor s : LIVE) s.read();
    } catch (Throwable t) {
      markUnavailable("the robot-loop step: " + describe(t));
    }
  }

  private static String describe(Throwable t) {
    while (t instanceof InvocationTargetException && t.getCause() != null) t = t.getCause();
    return t.getClass().getSimpleName() + (t.getMessage() != null ? ": " + t.getMessage() : "");
  }

  // ------------------------------------------------------------------ one LaserCAN

  private static final class Sensor {
    final int id;
    Object distanceMm, status;   // the SimInt inputs (robot thread)
    // what robot code set (any thread), and scrimmage's last values, taken on the robot loop (read on any thread)
    volatile boolean isLong;     // the firmware starts in short ranging
    volatile int budgetMs = DEFAULT_BUDGET_MS;
    volatile int[] roi = DEFAULT_ROI;
    volatile long reading = NO_READING;  // (status << 32) | distance_mm, one write so a reader sees a matched pair

    Sensor(int id) {
      this.id = id;
    }

    /** Make the SimDevice; false (said once) when it can't be made: two LaserCANs with that id. */
    boolean open() throws Exception {
      String name = "ScrimmageGrapple:LaserCAN[" + id + "]";
      Object device = api.simCreate.invoke(null, name);
      if (device == null) {
        System.out.println("scrimmage grapple helper: LaserCAN " + id + " is not simulated: can't create its "
            + "scrimmage device " + name + " (two LaserCANs with that id?)");
        return false;
      }
      distanceMm = api.createInt.invoke(device, "distance_mm", api.kInput, UNWRITTEN);
      status = api.createInt.invoke(device, "status", api.kInput, UNWRITTEN);
      return true;
    }

    void read() throws Exception {
      int mm = (int) api.intGet.invoke(distanceMm);
      int st = (int) api.intGet.invoke(status);
      if (mm != UNWRITTEN && st != UNWRITTEN) reading = ((long) st << 32) | (mm & 0xFFFFFFFFL);
    }

    Object measurement() throws Exception {
      long r = reading;
      if (r == NO_READING) return null;
      int st = (int) (r >> 32);
      int mm = (int) r;
      boolean lng = isLong;
      int reach = lng ? LONG_REACH_MM : SHORT_REACH_MM;
      if (st == STATUS_NO_TARGET || mm > reach) {  // nothing within the mode's reach: no target, at the reach
        st = STATUS_NO_TARGET;
        mm = reach;
      }
      int[] r4 = roi;
      Object region = api.roiNew.newInstance(r4[0], r4[1], r4[2], r4[3]);
      return api.measurementNew.newInstance(st, mm, 0, lng, budgetMs, region);
    }
  }

  // ------------------------------------------------------------------ libgrapplefrc / WPILib by reflection

  private static final class Missing extends Exception {
    Missing(String what) {
      super(what);
    }
  }

  private static final class Api {
    final Constructor<?> measurementNew, roiNew;
    final Field roiX, roiY, roiW, roiH;
    final Method asMilliseconds;
    final Method simCreate, createInt, intGet;
    final Object kInput;
    final Method registerCallback;
    private final ClassLoader loader;

    Api(ClassLoader loader) throws Missing {
      this.loader = loader;
      Class<?> roi = cls("au.grapplerobotics.interfaces.LaserCanInterface$RegionOfInterest");
      measurementNew = constructor(cls("au.grapplerobotics.interfaces.LaserCanInterface$Measurement"),
          int.class, int.class, int.class, boolean.class, int.class, roi);
      roiNew = constructor(roi, int.class, int.class, int.class, int.class);
      roiX = field(roi, "x");
      roiY = field(roi, "y");
      roiW = field(roi, "w");
      roiH = field(roi, "h");
      asMilliseconds = method(cls("au.grapplerobotics.interfaces.LaserCanInterface$TimingBudget"), "asMilliseconds");
      Class<?> simDevice = cls("edu.wpi.first.hal.SimDevice");
      Class<?> direction = cls("edu.wpi.first.hal.SimDevice$Direction");
      simCreate = method(simDevice, "create", String.class);
      createInt = method(simDevice, "createInt", String.class, direction, int.class);
      kInput = constant(direction, "kInput");
      intGet = method(cls("edu.wpi.first.hal.SimInt"), "get");
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
        throw new Missing("no method " + c.getName() + "." + name);
      }
    }

    private static Constructor<?> constructor(Class<?> c, Class<?>... params) throws Missing {
      try {
        return c.getConstructor(params);
      } catch (NoSuchMethodException | LinkageError e) {
        throw new Missing("no constructor " + c.getName() + " with " + params.length + " arguments");
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
  }
}
