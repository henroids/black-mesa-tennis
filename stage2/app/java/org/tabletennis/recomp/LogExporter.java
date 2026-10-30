package org.tabletennis.recomp;

import android.content.Context;
import android.net.Uri;
import android.os.Build;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/** Writes one zip with the runtime logs, recent logcat and a device/settings report. */
final class LogExporter {
    private LogExporter() {}

    static String export(Context c, Uri destination) {
        int count = 0;
        OutputStream raw;
        try {
            raw = c.getContentResolver().openOutputStream(destination, "w");
        } catch (IOException e) {
            raw = null;
        }
        if (raw == null) return "Could not open the destination file.";
        try (ZipOutputStream zip = new ZipOutputStream(raw)) {
            for (File dir : new File[]{GameFiles.logsDir(c), new File(GameFiles.baseDir(c), "logs")}) {
                File[] files = dir.listFiles(File::isFile);
                if (files == null) continue;
                for (File f : files) {
                    addFile(zip, dir.getParentFile().getName() + "_" + f.getName(), f);
                    count++;
                }
                File[] sub = dir.listFiles(File::isDirectory);
                if (sub != null) for (File d : sub) {
                    File[] inner = d.listFiles(File::isFile);
                    if (inner == null) continue;
                    for (File f : inner) {
                        addFile(zip, d.getName() + "/" + f.getName(), f);
                        count++;
                    }
                }
            }
            addText(zip, "report.txt", report(c));
            addText(zip, "logcat.txt", logcat());
            return "Saved " + count + " log file(s) plus logcat and a device report.";
        } catch (IOException e) {
            return "Log export failed: " + e.getMessage();
        }
    }

    private static String report(Context c) {
        StringBuilder r = new StringBuilder();
        r.append("Table Tennis Android port diagnostics\n");
        r.append("Device: ").append(Build.MANUFACTURER).append(' ').append(Build.MODEL)
                .append(" (").append(Build.HARDWARE).append(")\n");
        r.append("Android: ").append(Build.VERSION.RELEASE).append(" / API ").append(Build.VERSION.SDK_INT).append('\n');
        File iso = GameFiles.findIso(c);
        r.append("ISO: ").append(iso == null ? "not found" : iso.getAbsolutePath() + " (" + iso.length() + " bytes)").append('\n');
        r.append("Driver: ").append(DriverManager.selected(c)).append('\n');
        r.append("Installed drivers: ").append(DriverManager.installed(c)).append('\n');
        Settings s = Settings.load(c);
        r.append("Arguments: ").append(s.toArguments(iso == null ? null : iso.getAbsolutePath())).append('\n');
        r.append("TU_DEBUG: ").append(s.turnipDebug()).append('\n');
        return r.toString();
    }

    private static String logcat() {
        StringBuilder out = new StringBuilder();
        try {
            Process p = Runtime.getRuntime().exec(new String[]{"logcat", "-d", "-v", "threadtime", "-t", "8000"});
            try (BufferedReader in = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = in.readLine()) != null) out.append(line).append('\n');
            }
        } catch (IOException e) {
            out.append("logcat unavailable: ").append(e.getMessage());
        }
        return out.toString();
    }

    private static void addFile(ZipOutputStream zip, String name, File f) throws IOException {
        zip.putNextEntry(new ZipEntry(name));
        try (InputStream in = new FileInputStream(f)) {
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) zip.write(buf, 0, n);
        }
        zip.closeEntry();
    }

    private static void addText(ZipOutputStream zip, String name, String text) throws IOException {
        zip.putNextEntry(new ZipEntry(name));
        zip.write(text.getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
    }
}
