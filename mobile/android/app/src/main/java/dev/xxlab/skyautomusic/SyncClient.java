package dev.xxlab.skyautomusic;

import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Optional LAN pull client. Offline playback does not depend on this class. */
public final class SyncClient {
    public interface Callback {
        void onSuccess(Score score);

        void onError(String message);
    }

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    public void fetchCurrentScore(final String baseUrl, final String token, final Callback callback) {
        executor.execute(() -> {
            try {
                String raw = request(baseUrl, token, "/api/score");
                JSONObject object = new JSONObject(raw);
                String filename = object.optString("filename", "pc-synced.json");
                Score score = Score.fromJson(safeFilename(filename), raw);
                mainHandler.post(() -> callback.onSuccess(score));
            } catch (Exception error) {
                String message = error.getMessage();
                if (message == null || message.trim().isEmpty()) {
                    message = "无法从电脑读取当前曲谱";
                }
                final String finalMessage = message;
                mainHandler.post(() -> callback.onError(finalMessage));
            }
        });
    }

    public void shutdown() {
        executor.shutdownNow();
    }

    private static String request(String baseUrl, String token, String path) throws IOException {
        String normalized = normalizeBaseUrl(baseUrl);
        if (normalized.isEmpty()) {
            throw new IOException("请输入电脑同步地址");
        }
        URL url;
        try {
            url = new URI(normalized + path).toURL();
        } catch (URISyntaxException error) {
            throw new IOException("电脑地址格式无效", error);
        }
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("GET");
        connection.setConnectTimeout(6000);
        connection.setReadTimeout(10000);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("X-Sky-Token", token == null ? "" : token.trim());
        try {
            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            String body = readFully(stream);
            if (status < 200 || status >= 300) {
                try {
                    String error = new JSONObject(body).optString("error", "同步请求失败");
                    throw new IOException(error + " (HTTP " + status + ")");
                } catch (org.json.JSONException ignored) {
                    throw new IOException("同步请求失败 (HTTP " + status + ")");
                }
            }
            return body;
        } finally {
            connection.disconnect();
        }
    }

    private static String readFully(InputStream stream) throws IOException {
        if (stream == null) {
            return "";
        }
        StringBuilder result = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                result.append(line);
            }
        }
        return result.toString();
    }

    private static String normalizeBaseUrl(String value) {
        String normalized = value == null ? "" : value.trim();
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        if (!normalized.startsWith("http://") && !normalized.startsWith("https://")) {
            normalized = "http://" + normalized;
        }
        return normalized;
    }

    private static String safeFilename(String value) {
        String filename = value == null ? "pc-synced.json" : value.replace('\\', '/');
        int slash = filename.lastIndexOf('/');
        if (slash >= 0) {
            filename = filename.substring(slash + 1);
        }
        if (!filename.toLowerCase(java.util.Locale.ROOT).endsWith(".json")) {
            filename += ".json";
        }
        return filename.trim().isEmpty() ? "pc-synced.json" : filename;
    }
}
