package org.tabletennis.recomp;

import android.os.Bundle;
import android.system.ErrnoException;
import android.system.Os;
import android.util.Log;
import android.view.WindowManager;

import java.io.File;
import java.util.List;

import org.libsdl.app.SDLActivity;

/** SDL activity that hosts the recompiled game (libmain.so). */
public final class GameActivity extends SDLActivity {
    private static final String TAG = "TableTennis";

    /**
     * Keeps the previous session's profile and timeline (they're rewritten each run) in
     * logs/runs/, named by when that run's files were last written, so exported logs cover
     * several runs. Only the newest 8 of each are kept.
     */
    private static void archivePreviousRun(File logs) {
        File runs = new File(logs, "runs");
        String[] names = {"profile.txt", "timeline.txt"};
        java.text.SimpleDateFormat stamp =
                new java.text.SimpleDateFormat("MM-dd_HH-mm-ss", java.util.Locale.ROOT);
        for (String name : names) {
            File f = new File(logs, name);
            if (!f.isFile()) continue;
            runs.mkdirs();
            File to = new File(runs, stamp.format(new java.util.Date(f.lastModified())) + "_" + name);
            if (!f.renameTo(to)) f.delete();
        }
        File[] all = runs.listFiles(File::isFile);
        if (all == null) return;
        for (String name : names) {
            List<File> kind = new java.util.ArrayList<>();
            for (File f : all) if (f.getName().endsWith("_" + name)) kind.add(f);
            kind.sort((a, b) -> a.getName().compareTo(b.getName()));
            for (int i = 0; i < kind.size() - 8; i++) kind.get(i).delete();
        }
    }

    @Override protected void onCreate(Bundle savedInstanceState) {
        // Everything the native side reads from the environment must be set before
        // SDLActivity.onCreate loads libmain.so.
        Settings settings = Settings.load(this);
        File base = GameFiles.baseDir(this);
        setEnv("XDG_DATA_HOME", new File(base, "xdg").getAbsolutePath());
        setEnv("HOME", base.getAbsolutePath());
        if (settings.hiddenPasses == 1) setEnv("TT_DRAW_ALL_PASSES", "1");
        if (settings.test > 0) {  // TEMPORARY (v74)
            if (!Settings.TEST_VALUES[settings.test].isEmpty())
                setEnv("TT_TEST", Settings.TEST_VALUES[settings.test]);
            shotLabel = "Test: " + Settings.TEST_LABELS[settings.test];
        }
        if (settings.perfLog) {
            // Native sampling profiler: writes logs/profile.txt every 10 seconds.
            File logs = GameFiles.logsDir(this);
            logs.mkdirs();
            archivePreviousRun(logs);
            setEnv("TT_PROFILE", logs.getAbsolutePath());
            copySymbolLists(new File(logs, "syms"));
        }
        // Record this session's settings (plain words + environment) so exported logs say
        // exactly what each run used.
        String described = settings.describe(DriverManager.selected(this));
        Log.i(TAG, "Session settings: " + described);
        setEnv("TT_SETTINGS", described);
        File sessionLog = new File(GameFiles.logsDir(this), "sessions.txt");
        try {
            GameFiles.logsDir(this).mkdirs();
            try (java.io.FileWriter w = new java.io.FileWriter(sessionLog, true)) {
                w.write(new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.ROOT)
                        .format(new java.util.Date()) + "  " + described + "\n");
            }
        } catch (java.io.IOException e) {
            Log.w(TAG, "could not write sessions.txt", e);
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        super.onCreate(savedInstanceState);
        if (settings.perfLog) {
            GameFiles.logsDir(this).mkdirs();
            monitor = new ThreadMonitor(GameFiles.logsDir(this));
            monitor.start();
        }
    }

    private ThreadMonitor monitor;

    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemBars();
            // SDL can re-apply its own window flags after focus changes; hide again shortly after.
            getWindow().getDecorView().postDelayed(this::hideSystemBars, 600);
        }
    }

    @Override protected void onResume() {
        super.onResume();
        hideSystemBars();
    }

    /** Hide the status bar and the gesture/navigation bar while the game runs. */
    private void hideSystemBars() {
        if (android.os.Build.VERSION.SDK_INT < 30) return;
        android.view.WindowInsetsController c = getWindow().getInsetsController();
        if (c == null) return;
        c.hide(android.view.WindowInsets.Type.systemBars());
        c.setSystemBarsBehavior(android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
    }

    // ------------------------------------------------------------ return to start screen

    private boolean selectHeld, startHeld, thumbLHeld, thumbRHeld;
    private android.app.Dialog quitDialog;

    @Override public boolean dispatchKeyEvent(android.view.KeyEvent event) {
        int code = event.getKeyCode();
        boolean down = event.getAction() == android.view.KeyEvent.ACTION_DOWN;
        if (code == android.view.KeyEvent.KEYCODE_BUTTON_SELECT) selectHeld = down;
        if (code == android.view.KeyEvent.KEYCODE_BUTTON_START) startHeld = down;
        // Both stick buttons (L3 + R3) together: screenshot. Each press still reaches the game.
        if (code == android.view.KeyEvent.KEYCODE_BUTTON_THUMBL) {
            if (down && event.getRepeatCount() == 0 && thumbRHeld) takeScreenshot();
            thumbLHeld = down;
        }
        if (code == android.view.KeyEvent.KEYCODE_BUTTON_THUMBR) {
            if (down && event.getRepeatCount() == 0 && thumbLHeld) takeScreenshot();
            thumbRHeld = down;
        }
        if (quitDialog == null || !quitDialog.isShowing()) {
            if (down && selectHeld && startHeld) {
                selectHeld = startHeld = false;
                showQuitDialog();
                return true;
            }
            if (code == android.view.KeyEvent.KEYCODE_BACK) {
                if (down && event.getRepeatCount() == 0) showQuitDialog();
                return true;
            }
        }
        return super.dispatchKeyEvent(event);
    }

    @Override public void onBackPressed() { showQuitDialog(); }

    // ------------------------------------------------------------ screenshots (L3 + R3)

    // TEMPORARY (v74): the active test is written onto screenshots, because uploaded
    // pictures lose their file names (and with them the time stamp).
    private String shotLabel = "Test: Off";

    private final android.os.Handler shotHandler = new android.os.Handler(android.os.Looper.getMainLooper());

    /** Copies the game surface and saves it to Pictures/TableTennis (shows in the gallery). */
    private void takeScreenshot() {
        final android.view.SurfaceView surface = mSurface;
        if (surface == null || surface.getWidth() == 0 || surface.getHeight() == 0) return;
        final android.graphics.Bitmap bitmap = android.graphics.Bitmap.createBitmap(
                surface.getWidth(), surface.getHeight(), android.graphics.Bitmap.Config.ARGB_8888);
        android.view.PixelCopy.request(surface, bitmap, result -> {
            if (result != android.view.PixelCopy.SUCCESS) {
                toast("Screenshot failed");
                return;
            }
            new Thread(() -> {
                stampLabel(bitmap, shotLabel);
                String name = "tt_" + new java.text.SimpleDateFormat("yyyyMMdd_HHmmss",
                        java.util.Locale.ROOT).format(new java.util.Date()) + ".png";
                android.content.ContentValues values = new android.content.ContentValues();
                values.put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, name);
                values.put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/png");
                values.put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/TableTennis");
                android.net.Uri uri = getContentResolver().insert(
                        android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
                boolean ok = false;
                if (uri != null) {
                    try (java.io.OutputStream out = getContentResolver().openOutputStream(uri)) {
                        ok = out != null && bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out);
                    } catch (java.io.IOException e) {
                        Log.w(TAG, "screenshot not saved", e);
                    }
                }
                bitmap.recycle();
                final boolean saved = ok;
                runOnUiThread(() -> toast(saved ? "Screenshot saved: Pictures/TableTennis/" + name
                                                : "Screenshot failed"));
            }, "tt-screenshot").start();
        }, shotHandler);
    }

    /** Writes the label in the bottom-left corner: white pixel font on a black bar. */
    private void stampLabel(android.graphics.Bitmap bitmap, String label) {
        android.graphics.Canvas canvas = new android.graphics.Canvas(bitmap);
        android.graphics.Paint paint = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        try {
            paint.setTypeface(android.graphics.Typeface.createFromAsset(getAssets(), "fonts/GamePocket-Regular.ttf"));
        } catch (RuntimeException e) {
            paint.setTypeface(android.graphics.Typeface.MONOSPACE);
        }
        paint.setTextSize(bitmap.getHeight() / 30f);
        float pad = paint.getTextSize() / 2;
        float w = paint.measureText(label);
        float bottom = bitmap.getHeight() - pad;
        paint.setColor(0xFF000000);
        canvas.drawRect(pad, bottom - paint.getTextSize() - pad, pad * 3 + w, bottom + pad / 2, paint);
        paint.setColor(0xFFFFFFFF);
        canvas.drawText(label, pad * 2, bottom - pad / 4, paint);
    }

    private void toast(String text) {
        android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_SHORT).show();
    }

    // Return-to-start pop-up in the start screen's style: black, GamePocket font, inverted
    // white bar on the selected row, A to select, B to keep playing.
    private int quitChoice;  // 0 = keep playing, 1 = return
    private android.widget.TextView[] quitRows;

    private void showQuitDialog() {
        if (quitDialog != null && quitDialog.isShowing()) return;
        android.graphics.Typeface font;
        try {
            font = android.graphics.Typeface.createFromAsset(getAssets(), "fonts/GamePocket-Regular.ttf");
        } catch (RuntimeException e) {
            font = android.graphics.Typeface.MONOSPACE;
        }
        final int white = android.graphics.Color.rgb(250, 250, 250);
        final int grey = android.graphics.Color.rgb(154, 154, 154);
        final int iconGrey = android.graphics.Color.rgb(122, 122, 122);

        android.widget.LinearLayout panel = new android.widget.LinearLayout(this);
        panel.setOrientation(android.widget.LinearLayout.VERTICAL);
        panel.setPadding(dp(18), dp(16), dp(18), dp(12));
        android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
        bg.setColor(android.graphics.Color.BLACK);
        bg.setStroke(dp(1), android.graphics.Color.rgb(74, 74, 74));
        panel.setBackground(bg);

        android.widget.TextView title = quitText(font, 18, white);
        title.setText("Return to start screen?");
        panel.addView(title);
        android.widget.TextView note = quitText(font, 13, grey);
        note.setText("The game will close. The current match is lost.");
        note.setPadding(0, dp(6), 0, dp(12));
        panel.addView(note);

        String[] labels = {"Keep playing", "Return to start screen"};
        quitRows = new android.widget.TextView[labels.length];
        for (int i = 0; i < labels.length; i++) {
            android.widget.TextView row = quitText(font, 17, white);
            row.setText(labels[i]);
            row.setPadding(dp(10), dp(3), dp(10), dp(3));
            final int index = i;
            row.setOnClickListener(v -> {
                if (quitChoice == index) chooseQuit(); else { quitChoice = index; renderQuit(); }
            });
            row.setFocusable(false);
            row.setDefaultFocusHighlightEnabled(false);
            quitRows[i] = row;
            panel.addView(row, new android.widget.LinearLayout.LayoutParams(-1, -2));
        }

        android.widget.LinearLayout help = new android.widget.LinearLayout(this);
        help.setGravity(android.view.Gravity.CENTER_VERTICAL | android.view.Gravity.END);
        help.setPadding(0, dp(12), 0, 0);
        help.addView(quitGlyph(font, "A", iconGrey));
        help.addView(quitHelp(font, "Select", grey));
        help.addView(quitGlyph(font, "B", iconGrey));
        help.addView(quitHelp(font, "Back", grey));
        panel.addView(help, new android.widget.LinearLayout.LayoutParams(-1, -2));

        quitDialog = new android.app.Dialog(this, android.R.style.Theme_Translucent_NoTitleBar);
        android.widget.FrameLayout dim = new android.widget.FrameLayout(this);
        dim.setBackgroundColor(android.graphics.Color.argb(170, 0, 0, 0));
        android.widget.FrameLayout.LayoutParams plp = new android.widget.FrameLayout.LayoutParams(
                dp(360), -2, android.view.Gravity.CENTER);
        dim.addView(panel, plp);
        quitDialog.setContentView(dim);
        quitDialog.setOnDismissListener(d -> hideSystemBars());
        quitDialog.setOnKeyListener((d, keyCode, event) -> {
            if (event.getAction() != android.view.KeyEvent.ACTION_DOWN) return true;
            switch (keyCode) {
                case android.view.KeyEvent.KEYCODE_DPAD_UP:
                case android.view.KeyEvent.KEYCODE_DPAD_DOWN:
                case android.view.KeyEvent.KEYCODE_DPAD_LEFT:
                case android.view.KeyEvent.KEYCODE_DPAD_RIGHT:
                    quitChoice = 1 - quitChoice; renderQuit(); return true;
                case android.view.KeyEvent.KEYCODE_BUTTON_A:
                case android.view.KeyEvent.KEYCODE_DPAD_CENTER:
                case android.view.KeyEvent.KEYCODE_ENTER:
                case android.view.KeyEvent.KEYCODE_BUTTON_START:
                    chooseQuit(); return true;
                case android.view.KeyEvent.KEYCODE_BUTTON_B:
                case android.view.KeyEvent.KEYCODE_BACK:
                    d.dismiss(); return true;
                default:
                    return true;
            }
        });
        quitChoice = 0;
        renderQuit();
        quitDialog.show();
        android.view.Window w = quitDialog.getWindow();
        if (w != null) {
            w.setLayout(-1, -1);
            if (android.os.Build.VERSION.SDK_INT >= 30 && w.getInsetsController() != null) {
                w.getInsetsController().hide(android.view.WindowInsets.Type.systemBars());
            }
        }
    }

    private void renderQuit() {
        if (quitRows == null) return;
        for (int i = 0; i < quitRows.length; i++) {
            boolean on = i == quitChoice;
            quitRows[i].setBackgroundColor(on ? android.graphics.Color.rgb(250, 250, 250)
                                              : android.graphics.Color.BLACK);
            quitRows[i].setTextColor(on ? android.graphics.Color.BLACK
                                        : android.graphics.Color.rgb(250, 250, 250));
        }
    }

    private void chooseQuit() {
        if (quitChoice == 1) returnToLauncher();
        else if (quitDialog != null) quitDialog.dismiss();
    }

    private android.widget.TextView quitText(android.graphics.Typeface font, int sp, int color) {
        android.widget.TextView t = new android.widget.TextView(this);
        t.setTypeface(font);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setIncludeFontPadding(false);
        return t;
    }

    private android.widget.TextView quitHelp(android.graphics.Typeface font, String s, int color) {
        android.widget.TextView t = quitText(font, 14, color);
        t.setText(s);
        t.setPadding(dp(4), 0, dp(12), 0);
        return t;
    }

    private android.widget.TextView quitGlyph(android.graphics.Typeface font, String s, int color) {
        android.widget.TextView t = quitText(font, 11, android.graphics.Color.BLACK);
        t.setText(s);
        t.setGravity(android.view.Gravity.CENTER);
        android.graphics.drawable.GradientDrawable g = new android.graphics.drawable.GradientDrawable();
        g.setColor(color);
        g.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        t.setBackground(g);
        t.setMinWidth(dp(20));
        t.setMinHeight(dp(20));
        return t;
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    private void returnToLauncher() {
        android.content.Intent home = new android.content.Intent(this, LauncherActivity.class)
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                        | android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP);
        // Clear the "custom driver in use" marker first: the runtime's own shutdown can
        // abort while closing, which made the launcher think the driver had crashed and
        // silently switch back to the system driver.
        new File(DriverManager.root(this), "custom-driver-starting").delete();
        if (monitor != null) monitor.shutdown();
        startActivity(home);
        // End the game process right away instead of running the native shutdown.
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    @Override protected String[] getLibraries() {
        // SDL3 is linked statically into the runtime, so only the game library is loaded here.
        return new String[]{"main"};
    }

    @Override protected String[] getArguments() {
        File iso = GameFiles.findIso(this);
        List<String> args = Settings.load(this).toArguments(iso == null ? null : iso.getAbsolutePath());
        Log.i(TAG, "Launching with " + args);
        return args.toArray(new String[0]);
    }

    @Override protected void onDestroy() {
        if (monitor != null) monitor.shutdown();
        super.onDestroy();
        // The game runs in its own ":game" process. End it when the game closes so the
        // next launch starts clean (fresh runtime, freshly chosen GPU driver).
        if (isFinishing()) {
            // Normal exit: clear the "custom driver in use" marker. killProcess skips the
            // native atexit handler that would otherwise remove it, which made the launcher
            // think every session had crashed and fall back to the system driver.
            new File(DriverManager.root(this), "custom-driver-starting").delete();
            android.os.Process.killProcess(android.os.Process.myPid());
        }
    }

    /** Copies the runtime libraries' symbol lists (assets/syms) next to the logs once, so an
     *  exported log zip can be symbolized. SDK code only; no game code. */
    private void copySymbolLists(File dir) {
        try {
            String[] names = getAssets().list("syms");
            if (names == null) return;
            dir.mkdirs();
            for (String name : names) {
                File out = new File(dir, name);
                if (out.isFile() && out.length() > 0) continue;
                try (java.io.InputStream in = getAssets().open("syms/" + name);
                     java.io.OutputStream o = new java.io.FileOutputStream(out)) {
                    byte[] buf = new byte[1 << 16];
                    int n;
                    while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
                }
            }
        } catch (java.io.IOException e) {
            Log.w(TAG, "symbol lists not copied", e);
        }
    }

    private static void setEnv(String name, String value) {
        try {
            Os.setenv(name, value, true);
        } catch (ErrnoException e) {
            Log.w(TAG, "setenv " + name + " failed", e);
        }
    }
}
