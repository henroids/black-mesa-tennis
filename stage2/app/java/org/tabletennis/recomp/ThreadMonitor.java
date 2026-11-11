package org.tabletennis.recomp;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Every 5 seconds, writes which threads of the game process used the most CPU, plus the
 * current CPU clock speeds and temperature, to game/logs/cpu_threads.txt. This tells us
 * whether the game is limited by its own code, the GPU thread or shader compilation, and
 * whether the chip is throttling.
 */
final class ThreadMonitor extends Thread {
    private static final long INTERVAL_MS = 5000;  // first pass after 1 s so priorities apply early
    private final File out;
    private volatile boolean running = true;
    private final Map<String, Long> lastTicks = new HashMap<>();
    private final long clockTicks = 100;  // USER_HZ on Android

    ThreadMonitor(File logsDir) {
        super("tt-thread-monitor");
        setDaemon(true);
        out = new File(logsDir, "cpu_threads.txt");
        out.delete();
    }

    void shutdown() { running = false; interrupt(); }

    @Override public void run() {
        SimpleDateFormat time = new SimpleDateFormat("HH:mm:ss", Locale.ROOT);
        while (running) {
            try {
                Thread.sleep(INTERVAL_MS);
            } catch (InterruptedException e) {
                if (!running) return;
            }
            StringBuilder line = new StringBuilder();
            line.append(time.format(new Date())).append("  ").append(cpuFreqs()).append("  ")
                    .append(temperature()).append("  ").append(gpuBusy()).append('\n');
            List<String[]> usage = new ArrayList<>();
            double total = 0;
            File[] tasks = new File("/proc/self/task").listFiles();
            if (tasks != null) {
                for (File t : tasks) {
                    String stat = GameFiles.readFile(new File(t, "stat"));
                    int close = stat.lastIndexOf(')');
                    if (close < 0) continue;
                    String name = stat.substring(stat.indexOf('(') + 1, close);
                    String[] f = stat.substring(close + 2).split(" ");
                    if (f.length < 37) continue;
                    long ticks = Long.parseLong(f[11]) + Long.parseLong(f[12]);  // utime + stime
                    String key = t.getName();
                    if (!lastTicks.containsKey(key)) boost(Integer.parseInt(key), name);
                    Long prev = lastTicks.put(key, ticks);
                    if (prev == null) continue;
                    double pct = (ticks - prev) * 100.0 / clockTicks / (INTERVAL_MS / 1000.0);
                    total += pct;
                    if (pct >= 3) {
                        usage.add(new String[]{String.format(Locale.ROOT, "%5.0f%%", pct),
                                name + " [cpu" + f[36] + "]"});
                    }
                }
            }
            usage.sort((a, b) -> b[0].compareTo(a[0]));
            // Whole-app CPU load, so runs can be compared for strain as well as FPS.
            line.insert(line.length() - 1, String.format(Locale.ROOT, "  CPU: %.0f %% of one core", total));
            for (int i = 0; i < Math.min(12, usage.size()); i++) {
                line.append("   ").append(usage.get(i)[0]).append("  ").append(usage.get(i)[1]).append('\n');
            }
            try (FileWriter w = new FileWriter(out, true)) {
                w.write(line.toString());
            } catch (IOException ignored) { }
        }
    }

    /** Raise priority of the threads that matter; Android lets an app do this for its own threads. */
    private static void boost(int tid, String name) {
        int prio;
        if (name.startsWith("Audio Worker") || name.startsWith("XMA Decoder")) {
            prio = android.os.Process.THREAD_PRIORITY_URGENT_AUDIO;        // -19
        } else if (name.startsWith("GPU Commands") || name.startsWith("XThread")
                || name.startsWith("Main XThread")) {
            prio = android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY;      // -8
        } else {
            return;
        }
        try {
            android.os.Process.setThreadPriority(tid, prio);
        } catch (RuntimeException ignored) { }
    }

    /** Adreno load and clock (kgsl); empty when the kernel hides them. */
    private static String gpuBusy() {
        String busy = GameFiles.readFile(new File("/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage")).trim();
        String clk = GameFiles.readFile(new File("/sys/class/kgsl/kgsl-3d0/clock_mhz")).trim();
        if (busy.isEmpty() && clk.isEmpty()) return "GPU: ?";
        return "GPU: " + (busy.isEmpty() ? "?" : busy) + (clk.isEmpty() ? "" : " @ " + clk + " MHz");
    }

    private static String cpuFreqs() {
        StringBuilder s = new StringBuilder("MHz:");
        for (int i = 0; i < 8; i++) {
            String v = GameFiles.readFile(new File("/sys/devices/system/cpu/cpu" + i + "/cpufreq/scaling_cur_freq")).trim();
            s.append(' ').append(v.isEmpty() ? "?" : String.valueOf(Long.parseLong(v) / 1000));
        }
        return s.toString();
    }

    private static String temperature() {
        String best = "";
        for (int i = 0; i < 40; i++) {
            File zone = new File("/sys/class/thermal/thermal_zone" + i);
            String type = GameFiles.readFile(new File(zone, "type")).trim();
            if (type.isEmpty()) continue;
            if (type.contains("cpu") || type.contains("gpu") || type.contains("skin")) {
                String t = GameFiles.readFile(new File(zone, "temp")).trim();
                if (!t.isEmpty()) {
                    try {
                        long c = Long.parseLong(t);
                        best += " " + type + "=" + (c > 1000 ? c / 1000 : c) + "C";
                    } catch (NumberFormatException ignored) { }
                }
                if (best.length() > 120) break;
            }
        }
        return best.isEmpty() ? "temp: n/a" : "temp:" + best;
    }
}
