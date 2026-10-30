package org.tabletennis.recomp;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.net.Uri;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * Where the game's files live on the device.
 *
 * The native side treats getExternalFilesDir(null) (Android/data/<pkg>/files) as its
 * "executable folder": it looks for *.iso there and in game/, mounts the patched
 * flash.rpf from assets/resource/flash/, and keeps saves/cache in game/user_data and
 * game/cache.
 */
final class GameFiles {
    static final String ISO_NAME = "TableTennis.iso";
    private static final long XGD2_MAGIC_OFFSET = 0xFDA0000L;
    private static final byte[] MAGIC = "MICROSOFT*XBOX*MEDIA".getBytes(StandardCharsets.US_ASCII);

    interface Progress { void update(long copied, long total); }

    private GameFiles() {}

    static File baseDir(Context c) {
        File dir = c.getExternalFilesDir(null);
        return dir != null ? dir : c.getFilesDir();
    }

    static File gameDir(Context c) { return new File(baseDir(c), "game"); }

    static File logsDir(Context c) { return new File(gameDir(c), "logs"); }

    /** Any .iso in game/ or the base folder (so an ISO copied over USB also works). */
    static File findIso(Context c) {
        for (File dir : new File[]{gameDir(c), baseDir(c)}) {
            File[] files = dir.listFiles();
            if (files == null) continue;
            for (File f : files) {
                if (f.isFile() && f.getName().toLowerCase(Locale.ROOT).endsWith(".iso")
                        && f.length() > XGD2_MAGIC_OFFSET + MAGIC.length) {
                    return f;
                }
            }
        }
        return null;
    }

    static boolean looksLikeXgd2(File iso) {
        try (RandomAccessFile raf = new RandomAccessFile(iso, "r")) {
            if (raf.length() < XGD2_MAGIC_OFFSET + MAGIC.length) return false;
            byte[] buf = new byte[MAGIC.length];
            raf.seek(XGD2_MAGIC_OFFSET);
            raf.readFully(buf);
            return java.util.Arrays.equals(buf, MAGIC);
        } catch (IOException e) {
            return false;
        }
    }

    /** Folders the native runtime expects to already exist. */
    static void prepareFolders(Context c) {
        File game = gameDir(c);
        new File(game, "user_data").mkdirs();
        new File(game, "cache").mkdirs();
        logsDir(c).mkdirs();
        new File(baseDir(c), "xdg").mkdirs();
    }

    /** Copies the patched offline-menu flash.rpf out of the APK when it is missing or changed. */
    static void installFlashOverride(Context c) throws IOException {
        File target = new File(baseDir(c), "assets/resource/flash/flash.rpf");
        long assetLength;
        try (AssetFileDescriptor afd = c.getAssets().openFd("flash.rpf")) {
            assetLength = afd.getLength();
        } catch (IOException compressed) {
            assetLength = -1;  // stored compressed; fall back to always copying when absent
        }
        if (target.isFile() && (assetLength < 0 || target.length() == assetLength)) return;
        File parent = target.getParentFile();
        if (parent != null) parent.mkdirs();
        File tmp = new File(target.getPath() + ".tmp");
        try (InputStream in = c.getAssets().open("flash.rpf");
             OutputStream out = new FileOutputStream(tmp)) {
            copy(in, out, -1, null);
        }
        if (!tmp.renameTo(target)) throw new IOException("Could not install flash.rpf");
    }

    /** Copies a user-picked ISO into game/TableTennis.iso, then checks it. */
    static File importIso(Context c, Uri uri, Progress progress) throws IOException {
        File game = gameDir(c);
        if (!game.isDirectory() && !game.mkdirs()) throw new IOException("Cannot create " + game);
        long total = -1;
        try (AssetFileDescriptor afd = c.getContentResolver().openAssetFileDescriptor(uri, "r")) {
            if (afd != null) total = afd.getLength();
        } catch (IOException ignored) { }
        long free = game.getUsableSpace();
        if (total > 0 && free < total + (256L << 20)) {
            throw new IOException(String.format(Locale.ROOT,
                    "Not enough space: need %.1f GB, %.1f GB free.", total / 1e9, free / 1e9));
        }
        File tmp = new File(game, ISO_NAME + ".part");
        try (InputStream in = c.getContentResolver().openInputStream(uri);
             OutputStream out = new FileOutputStream(tmp)) {
            if (in == null) throw new IOException("Cannot open the selected file");
            copy(in, out, total, progress);
        }
        if (!looksLikeXgd2(tmp)) {
            tmp.delete();
            throw new IOException("That file is not an Xbox 360 (XGD2) game disc image.");
        }
        File iso = new File(game, ISO_NAME);
        File old = findIso(c);
        if (old != null && !old.equals(iso)) old.delete();
        if (iso.exists()) iso.delete();
        if (!tmp.renameTo(iso)) throw new IOException("Could not finish the ISO copy");
        return iso;
    }

    static void copy(InputStream in, OutputStream out, long total, Progress progress)
            throws IOException {
        byte[] buf = new byte[4 << 20];
        long copied = 0, lastReport = 0;
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            copied += n;
            if (progress != null && copied - lastReport > (64L << 20)) {
                progress.update(copied, total);
                lastReport = copied;
            }
        }
        if (out instanceof FileOutputStream) ((FileOutputStream) out).getFD().sync();
        if (progress != null) progress.update(copied, total);
    }

    static String readFile(File f) {
        try (FileInputStream in = new FileInputStream(f)) {
            // /proc and /sys files report a length of 0, so read until EOF.
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] b = new byte[8192];
            int n;
            while ((n = in.read(b)) > 0 && out.size() < (1 << 20)) out.write(b, 0, n);
            return out.toString("UTF-8");
        } catch (IOException e) {
            return "";
        }
    }
}
