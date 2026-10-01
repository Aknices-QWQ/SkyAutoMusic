package dev.xxlab.skyautomusic;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Standalone phone player. A PC is only needed for the optional score import button. */
public class MainActivity extends Activity implements PlayerEngine.Listener {
    private static final int IMPORT_REQUEST = 41;
    private static final String PREFS = "sky_mobile";

    private final ArrayList<ScoreEntry> scores = new ArrayList<>();
    private final ArrayList<String> scoreLabels = new ArrayList<>();
    private final PlayerEngine player = new PlayerEngine();
    private final SyncClient syncClient = new SyncClient();

    private SharedPreferences preferences;
    private Spinner scoreSpinner;
    private ArrayAdapter<String> scoreAdapter;
    private SeekBar progressBar;
    private SeekBar speedBar;
    private TextView progressLabel;
    private TextView speedLabel;
    private TextView statusLabel;
    private TextView accessibilityLabel;
    private EditText pcUrlInput;
    private EditText pcTokenInput;
    private Button syncButton;
    private Score currentScore;
    private float[] coordinateX = new float[15];
    private float[] coordinateY = new float[15];
    private File importedDirectory;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        importedDirectory = new File(getFilesDir(), "imported_scores");
        if (!importedDirectory.exists()) {
            //noinspection ResultOfMethodCallIgnored
            importedDirectory.mkdirs();
        }
        loadCoordinates();
        buildInterface();
        loadScores();
        updateAccessibilityStatus();
    }

    private void buildInterface() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int padding = dp(20);
        root.setPadding(padding, dp(18), padding, dp(28));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(padding, dp(18) + insets.getSystemWindowInsetTop(), padding,
                    dp(28) + insets.getSystemWindowInsetBottom());
            return insets;
        });

        TextView title = text("SkyAutoMusic Mobile", 24, Color.WHITE);
        root.addView(title, matchWrap());
        TextView offline = text("手机可独立运行：曲谱保存在本机，无需连接电脑。", 14, Color.LTGRAY);
        offline.setPadding(0, dp(6), 0, dp(14));
        root.addView(offline, matchWrap());

        root.addView(text("本地曲谱", 17, Color.WHITE), matchWrap());
        scoreSpinner = new Spinner(this);
        scoreSpinner.setContentDescription("选择本地曲谱");
        scoreAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, scoreLabels);
        scoreAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        scoreSpinner.setAdapter(scoreAdapter);
        scoreSpinner.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                if (position >= 0 && position < scores.size()) {
                    chooseScore(position);
                }
            }

            @Override
            public void onNothingSelected(android.widget.AdapterView<?> parent) {
                currentScore = null;
            }
        });
        root.addView(scoreSpinner, matchWrap());

        progressLabel = text("进度 0%", 14, Color.LTGRAY);
        progressLabel.setPadding(0, dp(14), 0, 0);
        root.addView(progressLabel, matchWrap());
        progressBar = new SeekBar(this);
        progressBar.setMax(100);
        progressBar.setContentDescription("曲谱进度");
        progressBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int value, boolean fromUser) {
                updateProgressLabel(value);
            }

            @Override
            public void onStartTrackingTouch(SeekBar seekBar) {
            }

            @Override
            public void onStopTrackingTouch(SeekBar seekBar) {
                if (player.isRunning()) {
                    player.stop();
                    statusLabel.setText("已定位，点击播放从此处继续");
                }
            }
        });
        root.addView(progressBar, matchWrap());

        speedLabel = text("速度 1.00x", 14, Color.LTGRAY);
        speedLabel.setPadding(0, dp(8), 0, 0);
        root.addView(speedLabel, matchWrap());
        speedBar = new SeekBar(this);
        speedBar.setMax(200);
        speedBar.setProgress(Math.max(25, Math.min(200, Math.round(preferences.getFloat("speed", 1f) * 100f))));
        speedBar.setContentDescription("播放速度");
        speedBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int value, boolean fromUser) {
                if (value < 25) {
                    seekBar.setProgress(25);
                    return;
                }
                updateSpeedLabel();
            }

            @Override
            public void onStartTrackingTouch(SeekBar seekBar) {
            }

            @Override
            public void onStopTrackingTouch(SeekBar seekBar) {
                preferences.edit().putFloat("speed", speed()).apply();
            }
        });
        updateSpeedLabel();
        root.addView(speedBar, matchWrap());

        LinearLayout playbackRow = row();
        Button playButton = button("播放");
        playButton.setContentDescription("播放当前曲谱");
        playButton.setOnClickListener(view -> startPlayback());
        Button stopButton = button("停止");
        stopButton.setContentDescription("停止播放");
        stopButton.setOnClickListener(view -> stopPlayback());
        playbackRow.addView(playButton, weightWrap());
        playbackRow.addView(stopButton, weightWrap());
        root.addView(playbackRow, matchWrap());

        statusLabel = text("准备就绪", 14, Color.WHITE);
        statusLabel.setPadding(0, dp(10), 0, dp(8));
        statusLabel.setContentDescription("播放状态");
        root.addView(statusLabel, matchWrap());

        LinearLayout fileRow = row();
        Button importButton = button("导入 JSON 曲谱");
        importButton.setContentDescription("从手机文件导入 JSON 曲谱");
        importButton.setOnClickListener(view -> chooseScoreFile());
        Button calibrateButton = button("校准 15 键坐标");
        calibrateButton.setContentDescription("编辑屏幕点击坐标");
        calibrateButton.setOnClickListener(view -> showCalibrationDialog());
        fileRow.addView(importButton, weightWrap());
        fileRow.addView(calibrateButton, weightWrap());
        root.addView(fileRow, matchWrap());

        LinearLayout accessRow = row();
        Button accessButton = button("打开无障碍设置");
        accessButton.setContentDescription("打开系统无障碍设置");
        accessButton.setOnClickListener(view -> openAccessibilitySettings());
        accessibilityLabel = text("检查中", 13, Color.LTGRAY);
        accessibilityLabel.setGravity(android.view.Gravity.CENTER_VERTICAL);
        accessRow.addView(accessButton, weightWrap());
        accessRow.addView(accessibilityLabel, weightWrap());
        root.addView(accessRow, matchWrap());

        root.addView(text("可选：从电脑同步", 17, Color.WHITE), topMarginWrap(20));
        TextView syncHint = text("电脑端启动“手机同步”后，把局域网地址和令牌填在这里。同步完成后曲谱会保存到手机，之后可断开电脑独立播放。", 13, Color.LTGRAY);
        syncHint.setPadding(0, dp(6), 0, dp(8));
        syncHint.setContentDescription("电脑同步说明");
        root.addView(syncHint, matchWrap());
        pcUrlInput = input("电脑地址，例如 http://192.168.1.20:8765", false);
        pcUrlInput.setText(preferences.getString("pc_url", ""));
        root.addView(pcUrlInput, matchWrap());
        pcTokenInput = input("配对令牌", false);
        pcTokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        pcTokenInput.setText(preferences.getString("pc_token", ""));
        root.addView(pcTokenInput, topMarginWrap(8));
        syncButton = button("同步电脑当前曲谱");
        syncButton.setContentDescription("从电脑同步当前曲谱");
        syncButton.setOnClickListener(view -> syncFromPc());
        root.addView(syncButton, topMarginWrap(8));

        scroll.addView(root, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
    }

    private void loadScores() {
        String previous = currentScore == null ? preferences.getString("selected_score", "") : currentScore.filename;
        scores.clear();
        scoreLabels.clear();
        try {
            AssetManager assets = getAssets();
            String[] names = assets.list("scores");
            if (names != null) {
                java.util.Arrays.sort(names, String.CASE_INSENSITIVE_ORDER);
                for (String name : names) {
                    if (!name.toLowerCase(Locale.ROOT).endsWith(".json")) {
                        continue;
                    }
                    addScore(new ScoreEntry(name, stem(name), false, null));
                }
            }
        } catch (IOException ignored) {
        }
        File[] imported = importedDirectory == null ? null : importedDirectory.listFiles((dir, name) -> name.toLowerCase(Locale.ROOT).endsWith(".json"));
        if (imported != null) {
            java.util.Arrays.sort(imported, (left, right) -> left.getName().compareToIgnoreCase(right.getName()));
            for (File file : imported) {
                addScore(new ScoreEntry(file.getName(), stem(file.getName()), true, null));
            }
        }
        scoreAdapter.notifyDataSetChanged();
        if (!scores.isEmpty()) {
            int selected = 0;
            for (int i = 0; i < scores.size(); i++) {
                if (scores.get(i).filename.equalsIgnoreCase(previous)) {
                    selected = i;
                    break;
                }
            }
            scoreSpinner.setSelection(selected);
            chooseScore(selected);
        } else {
            currentScore = null;
            statusLabel.setText("请导入或同步一份 JSON 曲谱");
        }
    }

    private void addScore(ScoreEntry entry) {
        if (entry == null) {
            return;
        }
        for (int i = 0; i < scores.size(); i++) {
            if (scores.get(i).filename.equalsIgnoreCase(entry.filename)) {
                scores.set(i, entry);
                scoreLabels.set(i, entry.title + "  ·  " + entry.filename);
                return;
            }
        }
        scores.add(entry);
        scoreLabels.add(entry.title + "  ·  " + entry.filename);
    }

    private void chooseScore(int position) {
        ScoreEntry entry = scores.get(position);
        try {
            if (entry.score == null) {
                String json;
                if (entry.imported) {
                    try (InputStream stream = new FileInputStream(new File(importedDirectory, entry.filename))) {
                        json = read(stream);
                    }
                } else {
                    try (InputStream stream = getAssets().open("scores/" + entry.filename)) {
                        json = read(stream);
                    }
                }
                entry.score = Score.fromJson(entry.filename, json);
                entry.title = entry.score.title;
                scoreLabels.set(position, entry.title + "  ·  " + entry.filename);
                scoreAdapter.notifyDataSetChanged();
                scoreSpinner.setSelection(position);
            }
            currentScore = entry.score;
            progressBar.setProgress(0);
            updateProgressLabel(0);
            statusLabel.setText("已选择：" + currentScore.title);
            preferences.edit().putString("selected_score", currentScore.filename).apply();
        } catch (Exception error) {
            currentScore = null;
            statusLabel.setText("曲谱读取失败：" + error.getMessage());
        }
    }

    private void startPlayback() {
        if (currentScore == null) {
            statusLabel.setText("请先选择曲谱");
            return;
        }
        if (!SkyAccessibilityService.isEnabled(this) || !SkyAccessibilityService.isConnected()) {
            statusLabel.setText("请先启用并返回 SkyAutoMusic 无障碍服务");
            openAccessibilitySettings();
            return;
        }
        if (player.isRunning()) {
            return;
        }
        player.start(currentScore, speed(), coordinateX, coordinateY, progressBar.getProgress(), this);
        statusLabel.setText("准备启动…");
    }

    private void stopPlayback() {
        player.stop();
        statusLabel.setText("已停止");
    }

    private void chooseScoreFile() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/json");
        startActivityForResult(intent, IMPORT_REQUEST);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != IMPORT_REQUEST || resultCode != RESULT_OK || data == null || data.getData() == null) {
            return;
        }
        Uri uri = data.getData();
        try (InputStream stream = getContentResolver().openInputStream(uri)) {
            if (stream == null) {
                throw new IOException("无法读取文件");
            }
            Score parsed = Score.fromJson("imported.json", read(stream));
            Score local = new Score(safeFilename(parsed.title), parsed.title, parsed.bpm, parsed.notes);
            saveImportedScore(local);
            loadScores();
            selectScore(local.filename);
            statusLabel.setText("已导入：" + local.title);
        } catch (Exception error) {
            Toast.makeText(this, "导入失败：" + error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void syncFromPc() {
        final String url = pcUrlInput.getText().toString().trim();
        final String token = pcTokenInput.getText().toString().trim();
        preferences.edit().putString("pc_url", url).putString("pc_token", token).apply();
        syncButton.setEnabled(false);
        statusLabel.setText("正在从电脑同步当前曲谱…");
        syncClient.fetchCurrentScore(url, token, new SyncClient.Callback() {
            @Override
            public void onSuccess(Score score) {
                Score local = new Score(safeFilename(score.filename), score.title, score.bpm, score.notes);
                try {
                    saveImportedScore(local);
                    loadScores();
                    selectScore(local.filename);
                    statusLabel.setText("同步完成：" + local.title + "（现在可断开电脑）");
                } catch (IOException error) {
                    statusLabel.setText("保存同步曲谱失败：" + error.getMessage());
                } finally {
                    syncButton.setEnabled(true);
                }
            }

            @Override
            public void onError(String message) {
                statusLabel.setText("同步失败：" + message);
                syncButton.setEnabled(true);
            }
        });
    }

    private void saveImportedScore(Score score) throws IOException {
        File target = new File(importedDirectory, safeFilename(score.filename));
        try (FileOutputStream output = new FileOutputStream(target)) {
            output.write(score.toJson().getBytes(StandardCharsets.UTF_8));
        }
    }

    private void selectScore(String filename) {
        for (int i = 0; i < scores.size(); i++) {
            if (scores.get(i).filename.equalsIgnoreCase(filename)) {
                scoreSpinner.setSelection(i);
                chooseScore(i);
                return;
            }
        }
    }

    private static String stem(String filename) {
        return filename != null && filename.toLowerCase(Locale.ROOT).endsWith(".json")
                ? filename.substring(0, filename.length() - 5) : filename;
    }

    private void showCalibrationDialog() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(14), dp(4), dp(14), dp(4));
        TextView hint = text("填写屏幕宽高百分比（0–100）。默认布局为 3 行 × 5 列。", 13, Color.DKGRAY);
        content.addView(hint, matchWrap());
        EditText[] xInputs = new EditText[15];
        EditText[] yInputs = new EditText[15];
        LinearLayout rows = new LinearLayout(this);
        rows.setOrientation(LinearLayout.VERTICAL);
        for (int i = 0; i < 15; i++) {
            LinearLayout line = new LinearLayout(this);
            line.setGravity(android.view.Gravity.CENTER_VERTICAL);
            TextView label = text("键 " + (i + 1), 13, Color.DKGRAY);
            line.addView(label, new LinearLayout.LayoutParams(dp(50), dp(48)));
            xInputs[i] = percentageInput(coordinateX[i]);
            yInputs[i] = percentageInput(coordinateY[i]);
            xInputs[i].setHint("X%");
            yInputs[i].setHint("Y%");
            line.addView(xInputs[i], weightWrap());
            line.addView(yInputs[i], weightWrap());
            rows.addView(line, matchWrap());
        }
        ScrollView scroll = new ScrollView(this);
        scroll.addView(rows, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        content.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(480)));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("校准 15 键坐标")
                .setView(content)
                .setNegativeButton("取消", null)
                .setPositiveButton("保存", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            try {
                for (int i = 0; i < 15; i++) {
                    float x = Float.parseFloat(xInputs[i].getText().toString()) / 100f;
                    float y = Float.parseFloat(yInputs[i].getText().toString()) / 100f;
                    if (x < 0 || x > 1 || y < 0 || y > 1) {
                        throw new NumberFormatException("坐标需在 0 到 100 之间");
                    }
                    coordinateX[i] = x;
                    coordinateY[i] = y;
                    preferences.edit().putFloat("x_" + i, x).putFloat("y_" + i, y).apply();
                }
                statusLabel.setText("坐标已保存");
                dialog.dismiss();
            } catch (NumberFormatException error) {
                Toast.makeText(this, "坐标格式无效：" + error.getMessage(), Toast.LENGTH_SHORT).show();
            }
        }));
        dialog.show();
    }

    private void loadCoordinates() {
        for (int i = 0; i < 15; i++) {
            int row = i / 5;
            int column = i % 5;
            coordinateX[i] = new float[]{0.15f, 0.325f, 0.50f, 0.675f, 0.85f}[column];
            coordinateY[i] = new float[]{0.33f, 0.50f, 0.67f}[row];
            if (preferences.contains("x_" + i)) {
                coordinateX[i] = preferences.getFloat("x_" + i, coordinateX[i]);
            }
            if (preferences.contains("y_" + i)) {
                coordinateY[i] = preferences.getFloat("y_" + i, coordinateY[i]);
            }
        }
    }

    private void openAccessibilitySettings() {
        try {
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
        } catch (Exception error) {
            Toast.makeText(this, "无法打开系统无障碍设置", Toast.LENGTH_LONG).show();
        }
    }

    private void updateAccessibilityStatus() {
        if (accessibilityLabel == null) {
            return;
        }
        boolean enabled = SkyAccessibilityService.isEnabled(this) && SkyAccessibilityService.isConnected();
        accessibilityLabel.setText(enabled ? "已启用" : "未启用");
        accessibilityLabel.setTextColor(enabled ? Color.rgb(40, 130, 70) : Color.rgb(160, 70, 50));
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateAccessibilityStatus();
    }

    @Override
    protected void onDestroy() {
        player.stop();
        syncClient.shutdown();
        super.onDestroy();
    }

    @Override
    public void onStatus(final String text) {
        runOnUiThread(() -> statusLabel.setText(text));
    }

    @Override
    public void onProgress(final int percent, final long elapsedMs, final String countText) {
        runOnUiThread(() -> {
            progressBar.setProgress(percent);
            progressLabel.setText("进度 " + percent + "%  · " + formatSeconds(elapsedMs) + "  · " + countText);
        });
    }

    @Override
    public void onFinished(final boolean stopped) {
        runOnUiThread(() -> {
            if (!stopped) {
                progressBar.setProgress(100);
                updateProgressLabel(100);
                statusLabel.setText("播放完成");
            } else if (statusLabel != null && !statusLabel.getText().toString().contains("失败")) {
                statusLabel.setText("已停止");
            }
            updateAccessibilityStatus();
        });
    }

    @Override
    public void onError(final String message) {
        runOnUiThread(() -> statusLabel.setText("播放失败：" + message));
    }

    private float speed() {
        return Math.max(0.25f, Math.min(2.0f, speedBar.getProgress() / 100f));
    }

    private void updateSpeedLabel() {
        speedLabel.setText(String.format(Locale.ROOT, "速度 %.2fx", speed()));
    }

    private void updateProgressLabel(int percent) {
        progressLabel.setText("进度 " + Math.max(0, Math.min(100, percent)) + "%");
    }

    private static String formatSeconds(long milliseconds) {
        long seconds = Math.max(0L, milliseconds) / 1000L;
        return String.format(Locale.ROOT, "%d:%02d", seconds / 60L, seconds % 60L);
    }

    private static String read(InputStream stream) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = stream.read(buffer)) != -1) {
            bytes.write(buffer, 0, count);
        }
        byte[] raw = bytes.toByteArray();
        if (raw.length >= 2) {
            if ((raw[0] & 0xff) == 0xff && (raw[1] & 0xff) == 0xfe) {
                return new String(raw, 2, raw.length - 2, java.nio.charset.StandardCharsets.UTF_16LE);
            }
            if ((raw[0] & 0xff) == 0xfe && (raw[1] & 0xff) == 0xff) {
                return new String(raw, 2, raw.length - 2, java.nio.charset.StandardCharsets.UTF_16BE);
            }
        }
        if (raw.length >= 3 && (raw[0] & 0xff) == 0xef && (raw[1] & 0xff) == 0xbb && (raw[2] & 0xff) == 0xbf) {
            return new String(raw, 3, raw.length - 3, StandardCharsets.UTF_8);
        }
        if (raw.length > 1 && raw[1] == 0) {
            return new String(raw, StandardCharsets.UTF_16LE);
        }
        if (raw.length > 0 && raw[0] == 0) {
            return new String(raw, StandardCharsets.UTF_16BE);
        }
        return new String(raw, StandardCharsets.UTF_8);
    }

    private static String safeFilename(String value) {
        String filename = value == null ? "imported.json" : value.replace('\\', '/');
        int slash = filename.lastIndexOf('/');
        if (slash >= 0) {
            filename = filename.substring(slash + 1);
        }
        filename = filename.replaceAll("[^\\p{L}\\p{N}._-]+", "_").trim();
        if (!filename.toLowerCase(Locale.ROOT).endsWith(".json")) {
            filename += ".json";
        }
        return filename.isEmpty() ? "imported.json" : filename;
    }

    private EditText input(String hint, boolean number) {
        EditText field = new EditText(this);
        field.setHint(hint);
        field.setSingleLine(true);
        field.setTextColor(Color.WHITE);
        field.setHintTextColor(Color.GRAY);
        field.setContentDescription(hint);
        if (number) {
            field.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        }
        return field;
    }

    private EditText percentageInput(float value) {
        EditText field = input("", true);
        field.setText(String.format(Locale.ROOT, "%.2f", value * 100f));
        return field;
    }

    private Button button(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        return button;
    }

    private TextView text(String value, float size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    private LinearLayout row() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(android.view.Gravity.CENTER_VERTICAL);
        return row;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams topMarginWrap(int margin) {
        LinearLayout.LayoutParams params = matchWrap();
        params.topMargin = dp(margin);
        return params;
    }

    private LinearLayout.LayoutParams weightWrap() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        params.setMargins(dp(3), dp(2), dp(3), dp(2));
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static final class ScoreEntry {
        final String filename;
        final boolean imported;
        String title;
        Score score;

        ScoreEntry(String filename, String title, boolean imported, Score score) {
            this.filename = filename;
            this.title = title;
            this.imported = imported;
            this.score = score;
        }
    }
}
