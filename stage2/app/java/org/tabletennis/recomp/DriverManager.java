package org.tabletennis.recomp;

import android.content.Context;
import android.net.Uri;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Custom Vulkan drivers (Turnip / Qualcomm AdrenoTools packages) imported by the user.
 *
 * Layout shared with the native loader (android_gpu_driver.cpp):
 *   filesDir/gpu_drivers/selected-driver.txt   "system" or a driver folder name
 *   filesDir/gpu_drivers/<name>/driver.txt      the .so file to load
 *   filesDir/gpu_drivers/<name>/<lib>.so
 *   filesDir/gpu_drivers/custom-driver-starting written while a custom driver runs;
 *                                               left behind if the game crashed
 */
final class DriverManager {
    static final String SYSTEM = "system";
    private static final long MAX_TOTAL = 300L << 20;

    private DriverManager() {}

    static File root(Context c) { return new File(c.getFilesDir(), "gpu_drivers"); }

    static List<String> installed(Context c) {
        List<String> names = new ArrayList<>();
        File[] dirs = root(c).listFiles(File::isDirectory);
        if (dirs != null) {
            for (File d : dirs) if (new File(d, "driver.txt").isFile()) names.add(d.getName());
        }
        java.util.Collections.sort(names);
        return names;
    }

    static String selected(Context c) {
        String s = GameFiles.readFile(new File(root(c), "selected-driver.txt")).trim();
        if (s.isEmpty() || (!SYSTEM.equals(s) && !installed(c).contains(s))) return SYSTEM;
        return s;
    }

    static void select(Context c, String name) throws IOException {
        File r = root(c);
        r.mkdirs();
        try (FileOutputStream out = new FileOutputStream(new File(r, "selected-driver.txt"))) {
            out.write((name + "\n").getBytes(StandardCharsets.UTF_8));
        }
    }

    /** True if the last session started with a custom driver and never exited cleanly. */
    static String consumeCrashMarker(Context c) {
        File marker = new File(root(c), "custom-driver-starting");
        if (!marker.isFile()) return null;
        String which = GameFiles.readFile(marker).trim();
        marker.delete();
        return which.isEmpty() ? "custom driver" : which;
    }

    /** Imports an AdrenoTools .zip (meta.json + .so) or a bare .so. Returns the folder name. */
    static String importDriver(Context c, Uri uri, String displayName) throws Exception {
        String base = displayName == null ? "driver" : displayName;
        base = base.replaceAll("(?i)\\.(zip|so)$", "");
        String name = base.replaceAll("[^A-Za-z0-9._-]+", "_");
        if (name.isEmpty() || name.equals(SYSTEM)) name = "driver";
        File r = root(c);
        File tmp = new File(r, name + ".tmp");
        deleteTree(tmp);
        if (!tmp.mkdirs()) throw new IOException("Cannot create driver folder");

        String library;
        boolean isSo = displayName != null && displayName.toLowerCase(Locale.ROOT).endsWith(".so");
        try (InputStream in = c.getContentResolver().openInputStream(uri)) {
            if (in == null) throw new IOException("Cannot open the selected file");
            if (isSo) {
                library = "libvulkan_custom.so";
                try (FileOutputStream out = new FileOutputStream(new File(tmp, library))) {
                    GameFiles.copy(in, out, -1, null);
                }
            } else {
                library = extractZip(in, tmp);
            }
        } catch (Exception e) {
            deleteTree(tmp);
            throw e;
        }
        try (FileOutputStream out = new FileOutputStream(new File(tmp, "driver.txt"))) {
            out.write((library + "\n").getBytes(StandardCharsets.UTF_8));
        }
        File target = new File(r, name);
        deleteTree(target);
        if (!tmp.renameTo(target)) throw new IOException("Could not activate the driver");
        return name;
    }

    static void delete(Context c, String name) {
        deleteTree(new File(root(c), name));
    }

    private static String extractZip(InputStream source, File dir) throws Exception {
        List<String> libraries = new ArrayList<>();
        String metaLibrary = null;
        long total = 0;
        try (ZipInputStream zip = new ZipInputStream(source)) {
            ZipEntry e;
            byte[] buf = new byte[1 << 16];
            while ((e = zip.getNextEntry()) != null) {
                if (e.isDirectory()) continue;
                String entry = e.getName();
                String file = entry.substring(entry.lastIndexOf('/') + 1);
                if (file.isEmpty() || file.startsWith(".") || entry.contains("..")) continue;
                File out = new File(dir, file);
                try (FileOutputStream o = new FileOutputStream(out)) {
                    int n;
                    while ((n = zip.read(buf)) > 0) {
                        total += n;
                        if (total > MAX_TOTAL) throw new IOException("Driver archive is too large");
                        o.write(buf, 0, n);
                    }
                }
                if (file.endsWith(".so")) libraries.add(file);
                if (file.equals("meta.json")) {
                    JSONObject meta = new JSONObject(GameFiles.readFile(out));
                    metaLibrary = meta.optString("libraryName", null);
                }
            }
        }
        if (metaLibrary != null && libraries.contains(metaLibrary)) return metaLibrary;
        for (String lib : libraries) {
            String l = lib.toLowerCase(Locale.ROOT);
            if (l.contains("vulkan") && !l.equals("libc++_shared.so")) return lib;
        }
        throw new IOException("No Vulkan driver (.so) found in that archive");
    }

    static void deleteTree(File f) {
        if (f == null || !f.exists()) return;
        File[] children = f.listFiles();
        if (children != null) for (File ch : children) deleteTree(ch);
        f.delete();
    }
}
