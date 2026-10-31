package org.tabletennis.recomp;

import android.app.Activity;
import android.app.Presentation;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.hardware.display.DisplayManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.text.format.DateFormat;
import android.view.Display;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * Start screen in the style of the Analogue OS menu: black, pixel font, an inverted white bar
 * for the selected row, values changed with left/right, A to select, B to back.
 * The Thor's second screen (if present) shows a title card.
 */
public final class LauncherActivity extends Activity {
    private static final int REQUEST_ISO = 1;
    private static final int REQUEST_DRIVER = 2;
    private static final int REQUEST_LOGS = 3;

    static final int BLACK = Color.BLACK;
    static final int WHITE = Color.rgb(250, 250, 250);
    static final int GREY = Color.rgb(154, 154, 154);
    static final int DIM = Color.rgb(74, 74, 74);
    static final int ICON = Color.rgb(122, 122, 122);

    private Settings settings;
    private Typeface font;
    private final List<Row> rows = new ArrayList<>();
    private int selected;
    private LinearLayout list;
    private TextView clock, battery, message;
    private boolean busy;
    private String isoProgress;
    private TitleCard titleCard;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable tick = new Runnable() {
        @Override public void run() { updateClock(); handler.postDelayed(this, 15000); }
    };

    /** One menu line. */
    private abstract static class Row {
        final String label;
        TextView labelView, valueView;
        LinearLayout view;
        Row(String label) { this.label = label; }
        String value() { return ""; }
        void activate() { }
        void change(int direction) { }
        boolean changeable() { return false; }
    }

    private final class Choice extends Row {
        final String[] values;
        final int[] index;
        final Runnable onChange;
        Choice(String label, String[] values, int[] index, Runnable onChange) {
            super(label);
            this.values = values; this.index = index; this.onChange = onChange;
        }
        @Override String value() { return values[index[0]]; }
        @Override boolean changeable() { return true; }
        @Override void change(int d) {
            index[0] = (index[0] + d + values.length) % values.length;
            onChange.run();
        }
        @Override void activate() { change(1); }
    }

    // Boxed settings so Choice rows can edit them in place.
    private final int[] resolution = new int[1], aa = new int[1], fps = new int[1],
            showFps = new int[1], driver = new int[1], shaderMode = new int[1],
            shaderThreads = new int[1], perfLog = new int[1], gameMsaa = new int[1],
            test = new int[1];
    private List<String> driverIds = new ArrayList<>();

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        settings = Settings.load(this);
        clearShaderCacheAfterUpdate();
        GameFiles.prepareFolders(this);
        try {
            font = Typeface.createFromAsset(getAssets(), "fonts/GamePocket-Regular.ttf");
        } catch (RuntimeException e) {
            font = Typeface.MONOSPACE;
        }
        resolution[0] = settings.resolution;
        aa[0] = settings.aa;
        fps[0] = settings.fps;
        showFps[0] = settings.showFps ? 1 : 0;
        shaderMode[0] = settings.shaderMode;
        shaderThreads[0] = settings.shaderThreads;
        perfLog[0] = settings.perfLog ? 1 : 0;
        gameMsaa[0] = settings.gameMsaa;
        test[0] = settings.test;
        loadDrivers();
        buildRows();
        buildScreen();
        String crashed = DriverManager.consumeCrashMarker(this);
        if (crashed != null) {
            try { DriverManager.select(this, DriverManager.SYSTEM); } catch (Exception ignored) { }
            loadDrivers();
            say("Last session with " + crashed + " didn't close cleanly. Using system driver.");
        }
        render();
    }

    @Override protected void onResume() {
        super.onResume();
        hideSystemBars();
        handler.post(tick);
        if (!busy) { loadDrivers(); render(); }
        showTitleCard();
    }

    @Override protected void onPause() {
        handler.removeCallbacks(tick);
        if (titleCard != null) { titleCard.dismiss(); titleCard = null; }
        super.onPause();
    }

    // ------------------------------------------------------------------ menu

    private void buildRows() {
        rows.clear();
        rows.add(new Row("Play") {
            @Override String value() { return GameFiles.findIso(LauncherActivity.this) == null ? "No disc" : ""; }
            @Override void activate() { launch(); }
        });
        rows.add(new Row("Game disc") {
            @Override String value() {
                if (isoProgress != null) return isoProgress;
                File iso = GameFiles.findIso(LauncherActivity.this);
                return iso == null ? "Choose" : iso.getName();
            }
            @Override void activate() { if (!busy) pick(REQUEST_ISO); }
        });
        Runnable save = this::saveSettings;
        rows.add(new Choice("Resolution", new String[]{"720p", "1440p (2x)"}, resolution, save));
        rows.add(new Choice("Anti-aliasing", new String[]{"Off", "FXAA", "FXAA extreme"}, aa, save));
        rows.add(new Choice("Frame rate", new String[]{"60 FPS", "30 FPS", "Uncapped"}, fps, save));
        rows.add(new Choice("Show FPS", new String[]{"Off", "On"}, showFps, save));
        rows.add(new Choice("Shaders", new String[]{"Async (may pop in)", "Wait (complete)"}, shaderMode, save));
        rows.add(new Choice("Shader threads", new String[]{"2", "4", "6"}, shaderThreads, save));
        rows.add(new Row("GPU driver") {
            @Override String value() { return driverLabel(driverIds.get(driver[0])); }
            @Override boolean changeable() { return true; }
            @Override void change(int d) {
                driver[0] = (driver[0] + d + driverIds.size()) % driverIds.size();
                try { DriverManager.select(LauncherActivity.this, driverIds.get(driver[0])); }
                catch (Exception e) { say("Couldn't save driver choice."); }
            }
            @Override void activate() { change(1); }
        });
        rows.add(new Row("Import driver") {
            @Override void activate() { if (!busy) pick(REQUEST_DRIVER); }
        });
        rows.add(new Row("Remove driver") {
            @Override String value() {
                String id = driverIds.get(driver[0]);
                return DriverManager.SYSTEM.equals(id) ? "" : driverLabel(id);
            }
            @Override void activate() { removeDriver(); }
        });
        rows.add(new Choice("Game MSAA", Settings.GAME_MSAA_LABELS, gameMsaa, save));
        rows.add(new Choice("Test (temporary)", Settings.TEST_LABELS, test, save));
        rows.add(new Choice("Performance log", new String[]{"Off", "On"}, perfLog, save));
        rows.add(new Row("Export logs") {
            @Override void activate() { exportLogs(); }
        });
        rows.add(new Row("Quit") {
            @Override void activate() { quitApp(); }
        });
    }

    private void saveSettings() {
        settings.resolution = resolution[0];
        settings.aa = aa[0];
        settings.fps = fps[0];
        settings.showFps = showFps[0] == 1;
        settings.shaderMode = shaderMode[0];
        settings.shaderThreads = shaderThreads[0];
        settings.perfLog = perfLog[0] == 1;
        settings.gameMsaa = gameMsaa[0];
        settings.test = test[0];
        settings.save(this);
    }

    private void loadDrivers() {
        driverIds = new ArrayList<>();
        driverIds.add(DriverManager.SYSTEM);
        driverIds.addAll(DriverManager.installed(this));
        driver[0] = Math.max(0, driverIds.indexOf(DriverManager.selected(this)));
    }

    private static String driverLabel(String id) {
        if (DriverManager.SYSTEM.equals(id)) return "System";
        return id.length() > 22 ? id.substring(0, 21) + "…" : id;
    }

    // ------------------------------------------------------------------ layout

    private void buildScreen() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BLACK);

        clock = text(14, GREY);
        root.addView(clock, frame(Gravity.TOP | Gravity.START, dp(14), dp(8), 0, 0));
        battery = text(14, GREY);
        root.addView(battery, frame(Gravity.TOP | Gravity.END, 0, dp(8), dp(14), 0));

        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        int screenW = getResources().getDisplayMetrics().widthPixels;
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP);
        lp.leftMargin = screenW / 4;
        lp.topMargin = dp(30);
        root.addView(list, lp);
        for (int i = 0; i < rows.size(); i++) addRowView(rows.get(i), i);

        message = text(13, GREY);
        message.setMaxLines(2);
        FrameLayout.LayoutParams mlp = frame(Gravity.BOTTOM | Gravity.START, dp(14), 0, 0, dp(10));
        mlp.width = screenW / 2;
        root.addView(message, mlp);

        LinearLayout help = new LinearLayout(this);
        help.setGravity(Gravity.CENTER_VERTICAL);
        help.addView(glyph("A", true)); help.addView(helpLabel("Select"));
        help.addView(glyph("B", true)); help.addView(helpLabel("Back"));
        help.addView(glyph("◀▶", false)); help.addView(helpLabel("Change"));
        root.addView(help, frame(Gravity.BOTTOM | Gravity.END, 0, 0, dp(14), dp(10)));

        setContentView(root);
    }

    private void addRowView(Row row, int index) {
        LinearLayout v = new LinearLayout(this);
        v.setOrientation(LinearLayout.HORIZONTAL);
        v.setGravity(Gravity.CENTER_VERTICAL);
        v.setPadding(dp(10), dp(2), dp(14), dp(2));
        row.labelView = text(17, WHITE);
        row.labelView.setText(row.label);
        row.valueView = text(17, GREY);
        row.valueView.setGravity(Gravity.END);
        row.valueView.setSingleLine(true);
        v.addView(row.labelView, new LinearLayout.LayoutParams(0, -2, 1f));
        v.addView(row.valueView, new LinearLayout.LayoutParams(-2, -2));
        v.setOnClickListener(view -> {
            if (selected == index) row.activate(); else selected = index;
            render();
        });
        // Our own white bar marks the selection; no system focus highlight (it drew a
        // green box around Play).
        v.setFocusable(false);
        v.setDefaultFocusHighlightEnabled(false);
        row.view = v;
        list.addView(v, new LinearLayout.LayoutParams(-1, -2));
    }

    private void render() {
        for (int i = 0; i < rows.size(); i++) {
            Row r = rows.get(i);
            boolean on = i == selected;
            r.view.setBackgroundColor(on ? WHITE : BLACK);
            r.labelView.setTextColor(on ? BLACK : WHITE);
            String value = r.value();
            if (on && r.changeable()) value = "‹ " + value + " ›";
            r.valueView.setText(value);
            r.valueView.setTextColor(on ? BLACK : GREY);
        }
        updateClock();
        if (titleCard != null) titleCard.update();
    }

    private void updateClock() {
        if (clock == null) return;
        clock.setText(DateFormat.getTimeFormat(this).format(new Date()));
        BatteryManager bm = (BatteryManager) getSystemService(BATTERY_SERVICE);
        int level = bm == null ? -1 : bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        battery.setText(level > 0 ? level + "%" : "");
    }

    // ------------------------------------------------------------------ input

    @Override public boolean onKeyDown(int keyCode, KeyEvent event) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_UP:
                selected = (selected - 1 + rows.size()) % rows.size(); render(); return true;
            case KeyEvent.KEYCODE_DPAD_DOWN:
                selected = (selected + 1) % rows.size(); render(); return true;
            case KeyEvent.KEYCODE_DPAD_LEFT:
                rows.get(selected).change(-1); render(); return true;
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                rows.get(selected).change(1); render(); return true;
            case KeyEvent.KEYCODE_BUTTON_A:
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_BUTTON_START:
                rows.get(selected).activate(); render(); return true;
            case KeyEvent.KEYCODE_BUTTON_B:
                if (selected != 0) { selected = 0; render(); return true; }
                finish(); return true;
            default:
                return super.onKeyDown(keyCode, event);
        }
    }

    // ------------------------------------------------------------------ actions

    private void launch() {
        if (busy) { say("Wait for the copy to finish."); return; }
        if (GameFiles.findIso(this) == null) { selected = 1; say("Choose your Table Tennis ISO first."); return; }
        try {
            GameFiles.prepareFolders(this);
            GameFiles.installFlashOverride(this);
        } catch (Exception e) {
            say("Couldn't prepare game files: " + e.getMessage());
            return;
        }
        startActivity(new Intent(this, GameActivity.class));
    }

    private void pick(int request) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        startActivityForResult(intent, request);
    }

    private void exportLogs() {
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/zip");
        intent.putExtra(Intent.EXTRA_TITLE, "tt-android-logs.zip");
        startActivityForResult(intent, REQUEST_LOGS);
    }

    private void removeDriver() {
        String current = driverIds.get(driver[0]);
        if (DriverManager.SYSTEM.equals(current)) { say("The system driver can't be removed."); return; }
        DriverManager.delete(this, current);
        try { DriverManager.select(this, DriverManager.SYSTEM); } catch (Exception ignored) { }
        loadDrivers();
        say("Removed " + current + ".");
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        if (requestCode == REQUEST_ISO) importIso(uri);
        else if (requestCode == REQUEST_DRIVER) importDriver(uri);
        else if (requestCode == REQUEST_LOGS) {
            say("Saving logs…");
            new Thread(() -> {
                String result = LogExporter.export(this, uri);
                runOnUiThread(() -> say(result));
            }, "tt-log-export").start();
        }
    }

    private void importIso(Uri uri) {
        setBusy(true);
        isoProgress = "Copying 0%";
        say("Copying the disc into the app. Keep the screen on.");
        render();
        new Thread(() -> {
            String result;
            try {
                File iso = GameFiles.importIso(this, uri, (copied, total) -> runOnUiThread(() -> {
                    isoProgress = total > 0
                            ? String.format(Locale.ROOT, "Copying %d%%", copied * 100 / total)
                            : String.format(Locale.ROOT, "Copying %.1f GB", copied / 1e9);
                    render();
                }));
                result = "Disc ready: " + iso.getName() + ".";
            } catch (Exception e) {
                result = "Disc import failed: " + e.getMessage();
            }
            final String done = result;
            runOnUiThread(() -> {
                isoProgress = null;
                setBusy(false);
                selected = 0;
                say(done);
                render();
            });
        }, "tt-iso-import").start();
    }

    private void importDriver(Uri uri) {
        String name = displayName(uri);
        setBusy(true);
        say("Importing " + name + "…");
        new Thread(() -> {
            String result;
            try {
                String id = DriverManager.importDriver(this, uri, name);
                DriverManager.select(this, id);
                result = "Driver " + id + " imported and selected.";
            } catch (Exception e) {
                result = "Driver import failed: " + e.getMessage();
            }
            final String done = result;
            runOnUiThread(() -> {
                setBusy(false);
                loadDrivers();
                say(done);
                render();
            });
        }, "tt-driver-import").start();
    }

    private void setBusy(boolean value) {
        busy = value;
        if (value) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private String displayName(Uri uri) {
        try (android.database.Cursor c = getContentResolver().query(uri,
                new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) return c.getString(0);
        } catch (Exception ignored) { }
        String last = uri.getLastPathSegment();
        return last == null ? "driver" : last;
    }

    /** Closes the launcher and any game process left in the background. */
    private void quitApp() {
        android.app.ActivityManager am = (android.app.ActivityManager) getSystemService(ACTIVITY_SERVICE);
        if (am != null && am.getRunningAppProcesses() != null) {
            for (android.app.ActivityManager.RunningAppProcessInfo p : am.getRunningAppProcesses()) {
                if (p.processName != null && p.processName.endsWith(":game")) {
                    new File(DriverManager.root(this), "custom-driver-starting").delete();
                    android.os.Process.killProcess(p.pid);
                }
            }
        }
        if (titleCard != null) { titleCard.dismiss(); titleCard = null; }
        finishAndRemoveTask();
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    /** A new build may translate shaders differently; drop cached translations once per install. */
    /** Bump only when a build changes how shaders are translated; any other update keeps the
     *  compiled shader cache, so menus don't stutter again after every new build. */
    private static final String SHADER_CACHE_VERSION = "tt-36";

    private void clearShaderCacheAfterUpdate() {
        try {
            android.content.SharedPreferences p = getSharedPreferences("tt_install", MODE_PRIVATE);
            if (SHADER_CACHE_VERSION.equals(p.getString("shader_cache_version", ""))) return;
            DriverManager.deleteTree(new File(GameFiles.gameDir(this), "cache"));
            DriverManager.deleteTree(new File(GameFiles.baseDir(this), "xdg"));
            p.edit().putString("shader_cache_version", SHADER_CACHE_VERSION).commit();
        } catch (Exception ignored) { }
    }

    private void say(String text) { if (message != null) message.setText(text); }

    // ------------------------------------------------------------------ second screen

    private void showTitleCard() {
        DisplayManager dm = (DisplayManager) getSystemService(DISPLAY_SERVICE);
        if (dm == null || titleCard != null) return;
        Display[] displays = dm.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION);
        if (displays.length == 0) return;
        try {
            titleCard = new TitleCard(this, displays[0]);
            titleCard.show();
        } catch (RuntimeException e) {
            titleCard = null;  // second screen busy or unavailable
        }
    }

    private final class TitleCard extends Presentation {
        private TextView info;
        TitleCard(Context context, Display display) { super(context, display); }

        @Override protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);
            LinearLayout box = new LinearLayout(getContext());
            box.setOrientation(LinearLayout.VERTICAL);
            box.setGravity(Gravity.CENTER_VERTICAL);
            box.setPadding(dp(28), dp(20), dp(28), dp(20));
            box.setBackgroundColor(BLACK);
            TextView title = text(30, WHITE);
            title.setText("Rockstar Table Tennis");
            box.addView(title);
            TextView sub = text(16, DIM);
            sub.setText("Rockstar Games · 2006");
            box.addView(sub);
            info = text(16, GREY);
            info.setPadding(0, dp(18), 0, 0);
            info.setLineSpacing(0, 1.3f);
            box.addView(info);
            setContentView(box);
            update();
        }

        void update() {
            if (info == null) return;
            File iso = GameFiles.findIso(LauncherActivity.this);
            String disc = isoProgress != null ? isoProgress
                    : iso == null ? "No disc yet" : String.format(Locale.ROOT, "Disc ready · %.2f GB", iso.length() / 1e9);
            info.setText(disc + "\nDriver: " + driverLabel(driverIds.get(driver[0]))
                    + "\nResolution: " + Settings.RESOLUTION_LABELS[resolution[0]].replaceAll(" \\(.*", ""));
        }
    }

    // ------------------------------------------------------------------ helpers

    private TextView text(int sp, int color) {
        TextView t = new TextView(this);
        t.setTypeface(font);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setIncludeFontPadding(false);
        return t;
    }

    private TextView helpLabel(String s) {
        TextView t = text(14, GREY);
        t.setText(s);
        t.setPadding(dp(4), 0, dp(14), 0);
        return t;
    }

    private TextView glyph(String s, boolean round) {
        TextView t = text(11, BLACK);
        t.setText(s);
        t.setGravity(Gravity.CENTER);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(ICON);
        if (round) bg.setShape(GradientDrawable.OVAL); else bg.setCornerRadius(dp(3));
        t.setBackground(bg);
        t.setMinWidth(dp(20));
        t.setMinHeight(dp(20));
        t.setPadding(dp(4), 0, dp(4), 0);
        return t;
    }

    private FrameLayout.LayoutParams frame(int gravity, int l, int t, int r, int b) {
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, gravity);
        lp.setMargins(l, t, r, b);
        return lp;
    }

    private void hideSystemBars() {
        if (android.os.Build.VERSION.SDK_INT < 30) return;
        WindowInsetsController c = getWindow().getInsetsController();
        if (c != null) {
            c.hide(WindowInsets.Type.systemBars());
            c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        }
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }
}
