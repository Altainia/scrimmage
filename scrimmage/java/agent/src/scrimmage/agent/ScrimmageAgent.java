package scrimmage.agent;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;

/**
 * scrimmage's sim-write guard. Under scrimmage the physics engine owns every simulated sensor, so robot-side
 * simulation models (CTRE's swerve sim thread, DCMotorSim pushed into getSimState(), ...) must not write them.
 * CTRE's license forbids modifying Phoenix's own classes, so they are never transformed. Instead, in the robot
 * program's own classes (everything but Phoenix, the JDK and this agent), calls to Phoenix 6 *SimState physics
 * setters become counted no-ops that give StatusCode.OK, and calls to a CTRE SwerveDrivetrain's
 * updateSimState(dt, volts) are skipped and counted. A summary line goes to the console for scrimmage.
 *
 * <p>REV: every SPARK the robot creates is handed to {@link ScrimmageRev} (a hook at the end of SparkBase's
 * constructor), which drives REV's SparkSim with scrimmage's physics; SparkSim.iterate called from anywhere else
 * is a counted no-op, and REV's simulated sensor noise is seeded with 0 so runs repeat.
 *
 * <p>Grapple: every LaserCan the robot creates is handed to {@link ScrimmageGrapple} (a hook at the end of its
 * constructor, with its CAN id); its getMeasurement() returns the helper's Measurement once scrimmage has written one,
 * and its ranging mode, timing budget and region-of-interest setters are recorded by the helper first.
 */
public final class ScrimmageAgent implements ClassFileTransformer {
  private static final String PKG = "com/ctre/phoenix6/sim/";
  private static final String STATUS = "com/ctre/phoenix6/StatusCode";
  // Only the devices scrimmage simulates; other Phoenix devices' robot-side sim models keep working.
  // (setMotorType / setMagnetHealth take enums, so the primitive-argument filter already excludes them;
  // the Talon's limit-switch setters are excluded by name: nothing in scrimmage drives those inputs.)
  private static final java.util.Set<String> PATCHED = java.util.Set.of(
      PKG + "TalonFXSimState", PKG + "CANcoderSimState", PKG + "Pigeon2SimState");
  private static final String SWERVE = "com.ctre.phoenix6.swerve.SwerveDrivetrain";
  // Never transformed: Phoenix (its license), the JDK, and this agent with its ASM.
  private static final String[] UNTOUCHED = {
      "com/ctre/", "java/", "javax/", "jdk/", "sun/", "com/sun/", "scrimmage/agent/", "org/objectweb/asm/"};
  private static final Map<Class<?>, Boolean> IS_SWERVE = new ConcurrentHashMap<>();
  private static final String SPARK_BASE = "com/revrobotics/spark/SparkBase";
  private static final String SPARK_SIM = "com/revrobotics/spark/SparkSim";
  private static final String NOISE = "com/revrobotics/sim/NoiseGenerator";
  private static final String LASER_CAN = "au/grapplerobotics/LaserCan";
  private static final String LCI = "au/grapplerobotics/interfaces/LaserCanInterface$";
  private static final String GRAPPLE = "scrimmage/agent/ScrimmageGrapple";

  static final Map<String, AtomicLong> COUNTS = new ConcurrentHashMap<>();
  static final Map<String, String> FIRST_CALLER = new ConcurrentHashMap<>();
  private static volatile long lastPrinted = -1;
  private static final AtomicBoolean FIRST_REPORT = new AtomicBoolean();

  public static void premain(String args, Instrumentation inst) {
    if (Boolean.getBoolean("scrimmage.agent.disable")) {
      System.out.println("scrimmage agent: disabled");
      return;
    }
    inst.addTransformer(new ScrimmageAgent());
    Thread t = new Thread(() -> {
      while (true) {
        try { Thread.sleep(5000); } catch (InterruptedException e) { return; }
        report();
      }
    }, "scrimmage-agent-report");
    t.setDaemon(true);
    t.start();
    Runtime.getRuntime().addShutdownHook(new Thread(() -> report()));
    System.out.println("scrimmage agent: loaded");
  }

  /** Called from patched methods. */
  public static void blocked(String key) {
    COUNTS.computeIfAbsent(key, k -> new AtomicLong()).incrementAndGet();
    if (FIRST_REPORT.compareAndSet(false, true)) {
      // one summary 100 ms after the first blocked write, so even short runs have one; then every 5 s
      Thread t = new Thread(() -> {
        try { Thread.sleep(100); } catch (InterruptedException e) { return; }
        report();
      }, "scrimmage-agent-first-report");
      t.setDaemon(true);
      t.start();
    }
    if (!FIRST_CALLER.containsKey(key)) {
      for (StackTraceElement f : Thread.currentThread().getStackTrace()) {
        String c = f.getClassName();
        if (!c.startsWith("com.ctre.") && !c.startsWith("com.revrobotics.") && !c.startsWith("scrimmage.agent.")
            && !c.startsWith("java.")
            && !c.startsWith("jdk.") && !c.startsWith("sun.")) {
          String m = f.getMethodName();
          if (m.startsWith("lambda$")) {  // lambda$startSimThread$5 -> startSimThread (lambda)
            String[] parts = m.split("\\$");
            m = (parts.length > 1 ? parts[1] : m) + " (lambda)";
          }
          FIRST_CALLER.putIfAbsent(key, c + "." + m);
          break;
        }
      }
    }
  }

  /** Called in place of robot code's {@code x.updateSimState(dt, volts)}: skipped (and counted) for a CTRE
   * SwerveDrivetrain, whose simulation would write the modules' sim state; any other class's method runs as usual. */
  public static void updateSimState(Object target, double dt, double volts) throws Throwable {
    Class<?> c = target.getClass();  // a null target throws NullPointerException, as the original call would
    if (IS_SWERVE.computeIfAbsent(c, ScrimmageAgent::isSwerve)) {
      blocked("SwerveDrivetrain.updateSimState");
      return;
    }
    for (Class<?> k = c; k != null; k = k.getSuperclass()) {
      try {
        java.lang.reflect.Method m = k.getDeclaredMethod("updateSimState", double.class, double.class);
        m.setAccessible(true);
        try {
          m.invoke(target, dt, volts);
        } catch (java.lang.reflect.InvocationTargetException e) {
          throw e.getCause();
        }
        return;
      } catch (NoSuchMethodException e) {
        // look in the superclass
      }
    }
    throw new NoSuchMethodError(c.getName() + ".updateSimState(DD)V");
  }

  private static boolean isSwerve(Class<?> c) {
    for (Class<?> k = c; k != null; k = k.getSuperclass()) {
      if (k.getName().equals(SWERVE)) return true;
    }
    return false;
  }

  static synchronized void report() {
    long total = COUNTS.values().stream().mapToLong(AtomicLong::get).sum();
    if (total == 0 || total == lastPrinted) return;
    lastPrinted = total;
    StringBuilder sb = new StringBuilder();
    COUNTS.entrySet().stream()
        .sorted((a, b) -> {
          int c = Long.compare(b.getValue().get(), a.getValue().get());
          return c != 0 ? c : a.getKey().compareTo(b.getKey());  // stable order when counts tie
        })
        .limit(3)
        .forEach(e -> {
          if (sb.length() > 0) sb.append("; ");
          sb.append(e.getKey()).append(" from ").append(FIRST_CALLER.getOrDefault(e.getKey(), "?"));
        });
    System.out.println("scrimmage agent: ignored " + total + " robot-side sim writes (" + sb + ")");
  }

  @Override
  public byte[] transform(ClassLoader loader, String name, Class<?> cls, ProtectionDomain pd, byte[] bytes) {
    if (name == null) return null;
    if (name.equals(SPARK_BASE)) return patchRev(bytes, (n, desc) -> n.equals("<init>") ? Hook.REGISTER : null);
    if (name.equals(SPARK_SIM)) {
      return patchRev(bytes, (n, desc) -> n.equals("iterate") && desc.equals("(DDD)V") ? Hook.GUARD_ITERATE : null);
    }
    if (name.equals(NOISE)) return patchRev(bytes, (n, desc) -> n.equals("<clinit>") ? Hook.SEED_NOISE : null);
    if (name.equals(LASER_CAN)) return patchGrapple(bytes);
    for (String prefix : UNTOUCHED) {
      if (name.startsWith(prefix)) return null;
    }
    if (loader == null || !mentionsSimWrites(bytes)) return null;
    return patchCallSites(bytes);
  }

  /** Whether a class file's constant pool can refer to a Phoenix sim setter or updateSimState (a cheap check, so
   * classes that can't are never parsed). */
  private static boolean mentionsSimWrites(byte[] bytes) {
    String text = new String(bytes, java.nio.charset.StandardCharsets.ISO_8859_1);
    return text.contains(PKG) || text.contains("updateSimState");
  }

  private static boolean isSimSetter(String owner, String n, String desc) {
    if (!PATCHED.contains(owner) || !(n.startsWith("set") || n.startsWith("add"))
        || n.endsWith("Limit")  // setForwardLimit/setReverseLimit: scrimmage has no limit-switch binding
        || !desc.endsWith(")L" + STATUS + ";")) {
      return false;
    }
    Type[] params = Type.getArgumentTypes(desc);
    return params.length == 1 && params[0].getSort() <= Type.DOUBLE;  // only primitive single-arg forms
    // (setMotorType / setMagnetHealth take enums, so they keep working)
  }

  /** Rewrite the robot program's calls to Phoenix sim setters and to updateSimState(dt, volts). The class's own
   * code is otherwise unchanged; the replacements leave the operand stack as the original calls did. */
  private static byte[] patchCallSites(byte[] bytes) {
    try {
      ClassReader cr = new ClassReader(bytes);
      ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
      boolean[] changed = {false};
      cr.accept(new ClassVisitor(Opcodes.ASM9, cw) {
        @Override
        public MethodVisitor visitMethod(int acc, String mn, String mdesc, String sig, String[] ex) {
          return new MethodVisitor(Opcodes.ASM9, super.visitMethod(acc, mn, mdesc, sig, ex)) {
            @Override
            public void visitMethodInsn(int op, String owner, String n, String desc, boolean itf) {
              if (op == Opcodes.INVOKEVIRTUAL && isSimSetter(owner, n, desc)) {
                // [target, value] -> [StatusCode.OK]
                super.visitInsn(Type.getArgumentTypes(desc)[0].getSize() == 2 ? Opcodes.POP2 : Opcodes.POP);
                super.visitInsn(Opcodes.POP);
                super.visitLdcInsn(owner.substring(owner.lastIndexOf('/') + 1) + "." + n);
                super.visitMethodInsn(Opcodes.INVOKESTATIC, "scrimmage/agent/ScrimmageAgent", "blocked",
                    "(Ljava/lang/String;)V", false);
                super.visitFieldInsn(Opcodes.GETSTATIC, STATUS, "OK", "L" + STATUS + ";");
                changed[0] = true;
              } else if (op == Opcodes.INVOKEVIRTUAL && n.equals("updateSimState") && desc.equals("(DD)V")) {
                super.visitMethodInsn(Opcodes.INVOKESTATIC, "scrimmage/agent/ScrimmageAgent", "updateSimState",
                    "(Ljava/lang/Object;DD)V", false);
                changed[0] = true;
              } else if (op == Opcodes.INVOKESPECIAL && n.equals("updateSimState") && desc.equals("(DD)V")
                  && owner.equals(SWERVE.replace('.', '/'))) {  // super.updateSimState(dt, volts)
                super.visitInsn(Opcodes.POP2);
                super.visitInsn(Opcodes.POP2);
                super.visitInsn(Opcodes.POP);
                super.visitLdcInsn("SwerveDrivetrain.updateSimState");
                super.visitMethodInsn(Opcodes.INVOKESTATIC, "scrimmage/agent/ScrimmageAgent", "blocked",
                    "(Ljava/lang/String;)V", false);
                changed[0] = true;
              } else {
                super.visitMethodInsn(op, owner, n, desc, itf);
              }
            }
          };
        }
      }, 0);
      return changed[0] ? cw.toByteArray() : null;
    } catch (Throwable t) {
      t.printStackTrace();
      return null;
    }
  }

  private enum Hook { REGISTER, GUARD_ITERATE, SEED_NOISE }

  private interface HookFor {
    Hook of(String name, String desc);
  }

  /** Patch a REV class: the methods `which` picks get the hook it names; everything else is left alone. */
  private static byte[] patchRev(byte[] bytes, HookFor which) {
    try {
      ClassReader cr = new ClassReader(bytes);
      ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
      cr.accept(new ClassVisitor(Opcodes.ASM9, cw) {
        boolean seedable;  // NoiseGenerator declares `static java.util.Random m_rand` (fields come before methods)

        @Override
        public org.objectweb.asm.FieldVisitor visitField(int acc, String n, String desc, String sig, Object v) {
          if (n.equals("m_rand") && desc.equals("Ljava/util/Random;") && (acc & Opcodes.ACC_STATIC) != 0) {
            seedable = true;
          }
          return super.visitField(acc, n, desc, sig, v);
        }

        @Override
        public MethodVisitor visitMethod(int acc, String n, String desc, String sig, String[] ex) {
          MethodVisitor mv = super.visitMethod(acc, n, desc, sig, ex);
          Hook hook = which.of(n, desc);
          if (hook == null || (acc & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) != 0) return mv;
          if (hook == Hook.SEED_NOISE && !seedable) {  // a PUTSTATIC to a missing field would break the class
            System.out.println("scrimmage agent: REV sim noise isn't seeded (NoiseGenerator has no static "
                + "java.util.Random m_rand); runs may not repeat exactly");
            return mv;
          }
          return new MethodVisitor(Opcodes.ASM9, mv) {
            @Override
            public void visitCode() {
              super.visitCode();
              if (hook != Hook.GUARD_ITERATE) return;
              // if (!ScrimmageRev.inside()) { ScrimmageAgent.blocked("SparkSim.iterate"); return; }
              org.objectweb.asm.Label run = new org.objectweb.asm.Label();
              super.visitMethodInsn(Opcodes.INVOKESTATIC, "scrimmage/agent/ScrimmageRev", "inside", "()Z", false);
              super.visitJumpInsn(Opcodes.IFNE, run);
              super.visitLdcInsn("SparkSim.iterate");
              super.visitMethodInsn(Opcodes.INVOKESTATIC, "scrimmage/agent/ScrimmageAgent", "blocked",
                  "(Ljava/lang/String;)V", false);
              super.visitInsn(Opcodes.RETURN);
              super.visitLabel(run);
              // the method's entry frame (no frame is computed: COMPUTE_MAXS), so the original frames still apply
              super.visitFrame(Opcodes.F_SAME, 0, null, 0, null);
            }

            @Override
            public void visitInsn(int op) {
              if (op == Opcodes.RETURN && hook == Hook.REGISTER) {
                super.visitVarInsn(Opcodes.ALOAD, 0);
                super.visitMethodInsn(Opcodes.INVOKESTATIC, "scrimmage/agent/ScrimmageRev", "register",
                    "(Ljava/lang/Object;)V", false);
              } else if (op == Opcodes.RETURN && hook == Hook.SEED_NOISE) {
                super.visitTypeInsn(Opcodes.NEW, "java/util/Random");
                super.visitInsn(Opcodes.DUP);
                super.visitInsn(Opcodes.LCONST_0);
                super.visitMethodInsn(Opcodes.INVOKESPECIAL, "java/util/Random", "<init>", "(J)V", false);
                super.visitFieldInsn(Opcodes.PUTSTATIC, NOISE, "m_rand", "Ljava/util/Random;");
              }
              super.visitInsn(op);
            }
          };
        }
      }, 0);
      return cw.toByteArray();
    } catch (Throwable t) {
      t.printStackTrace();
      return null;
    }
  }

  /** The helper method (in ScrimmageGrapple) a LaserCan setter's entry calls with (this, its argument), or null. */
  private static String grappleSetter(String n, String desc) {
    if (n.equals("setRangingMode") && desc.equals("(L" + LCI + "RangingMode;)V")) return "rangingMode";
    if (n.equals("setTimingBudget") && desc.equals("(L" + LCI + "TimingBudget;)V")) return "timingBudget";
    if (n.equals("setRegionOfInterest") && desc.equals("(L" + LCI + "RegionOfInterest;)V")) return "regionOfInterest";
    return null;
  }

  /** Patch Grapple's LaserCan: register each new one (with the id its constructor got), answer getMeasurement() from
   * the helper when it has a Measurement, and let the helper take the config setters first.
   *
   * Frames: the code put before a hooked method's own first instruction ends with an F_SAME frame at that instruction
   * (COMPUTE_MAXS computes none). That assumes the original first instruction is not a branch target, which would
   * already carry a StackMapTable frame there (two frames at one offset fail verification). In libgrapplefrc 2026.0.0
   * none is: getMeasurement and setRegionOfInterest have no frames, setRangingMode's first is at offset 12 and
   * setTimingBudget's at 40 (javap -v). A later release that loops back to offset 0 would need COMPUTE_FRAMES. */
  private static byte[] patchGrapple(byte[] bytes) {
    try {
      ClassReader cr = new ClassReader(bytes);
      ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS);
      cr.accept(new ClassVisitor(Opcodes.ASM9, cw) {
        @Override
        public MethodVisitor visitMethod(int acc, String n, String desc, String sig, String[] ex) {
          MethodVisitor mv = super.visitMethod(acc, n, desc, sig, ex);
          if ((acc & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) != 0) return mv;
          boolean init = n.equals("<init>") && desc.equals("(I)V");
          boolean measure = n.equals("getMeasurement") && desc.equals("()L" + LCI + "Measurement;");
          String setter = grappleSetter(n, desc);
          if (!init && !measure && setter == null) return mv;
          return new MethodVisitor(Opcodes.ASM9, mv) {
            @Override
            public void visitCode() {
              super.visitCode();
              if (init) return;
              org.objectweb.asm.Label original = new org.objectweb.asm.Label();
              super.visitVarInsn(Opcodes.ALOAD, 0);
              if (measure) {
                // Object m = ScrimmageGrapple.measurement(this); if (m != null) return (Measurement) m;
                super.visitMethodInsn(Opcodes.INVOKESTATIC, GRAPPLE, "measurement",
                    "(Ljava/lang/Object;)Ljava/lang/Object;", false);
                super.visitInsn(Opcodes.DUP);
                super.visitJumpInsn(Opcodes.IFNULL, original);
                super.visitTypeInsn(Opcodes.CHECKCAST, LCI + "Measurement");
                super.visitInsn(Opcodes.ARETURN);
                super.visitLabel(original);
                // the entry frame with the null on the stack, then the entry frame (COMPUTE_MAXS computes none)
                super.visitFrame(Opcodes.F_SAME1, 0, null, 1, new Object[] {"java/lang/Object"});
                super.visitInsn(Opcodes.POP);
              } else {
                // if (ScrimmageGrapple.<setter>(this, arg)) return;  (recorded; the CAN request would time out)
                super.visitVarInsn(Opcodes.ALOAD, 1);
                super.visitMethodInsn(Opcodes.INVOKESTATIC, GRAPPLE, setter,
                    "(Ljava/lang/Object;Ljava/lang/Object;)Z", false);
                super.visitJumpInsn(Opcodes.IFEQ, original);
                super.visitInsn(Opcodes.RETURN);
                super.visitLabel(original);
              }
              super.visitFrame(Opcodes.F_SAME, 0, null, 0, null);
            }

            @Override
            public void visitInsn(int op) {
              if (op == Opcodes.RETURN && init) {
                super.visitVarInsn(Opcodes.ALOAD, 0);
                super.visitVarInsn(Opcodes.ILOAD, 1);
                super.visitMethodInsn(Opcodes.INVOKESTATIC, GRAPPLE, "register", "(Ljava/lang/Object;I)V", false);
              }
              super.visitInsn(op);
            }
          };
        }
      }, 0);
      return cw.toByteArray();
    } catch (Throwable t) {
      t.printStackTrace();
      return null;
    }
  }
}
