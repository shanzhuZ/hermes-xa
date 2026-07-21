package com.example.aw.hbase;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.hbase.Cell;
import org.apache.hadoop.hbase.TableName;
import org.apache.hadoop.hbase.client.Connection;
import org.apache.hadoop.hbase.client.ConnectionFactory;
import org.apache.hadoop.hbase.client.Get;
import org.apache.hadoop.hbase.client.Result;
import org.apache.hadoop.hbase.client.Table;
import org.apache.hadoop.hbase.util.Bytes;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.annotation.PreDestroy;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.ReentrantLock;

/**
 * 图片原图读取：优先 HBase（短超时），失败/超时立刻回退本地，绝不长时间卡住调用方。
 * <p>
 * 读侧对齐 getRowData：遍历 Cell，用 offset/length 取 qualifier/value。
 */
@Component
public class HBaseImageClient {

    private static final Logger log = LoggerFactory.getLogger(HBaseImageClient.class);

    @Value("${hermes.hbase.enabled:true}")
    private boolean enabled;

    @Value("${hermes.hbase.zookeeper.quorum:192.168.3.171}")
    private String zkQuorum;

    @Value("${hermes.hbase.zookeeper.port:2181}")
    private String zkPort;

    @Value("${hermes.hbase.image-table:collect_image_bytes}")
    private String imageTable;

    @Value("${hermes.hbase.column-family:info}")
    private String columnFamily;

    @Value("${hermes.hbase.local-dir:}")
    private String localDir;

    /** 单次 HBase Get / 建连最长等待（毫秒） */
    @Value("${hermes.hbase.read-timeout-ms:3000}")
    private long readTimeoutMs;

    /** HBase 连续失败后冷却时间，冷却期内直接跳过 HBase 走本地 */
    @Value("${hermes.hbase.fail-cooldown-ms:60000}")
    private long failCooldownMs;

    private volatile Connection connection;

    /** 冷却截止时间戳；>now 时跳过 HBase */
    private volatile long hbaseSkipUntilMs = 0L;

    /** 建连锁：超时线程占坑时其它请求直接失败回退，避免排队卡死 */
    private final ReentrantLock connectLock = new ReentrantLock();

    private final ExecutorService ioPool = Executors.newCachedThreadPool(new ThreadFactory() {
        private final AtomicInteger seq = new AtomicInteger(1);

        @Override
        public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "hbase-image-io-" + seq.getAndIncrement());
            t.setDaemon(true);
            return t;
        }
    });

    /**
     * 按 RowKey 读原图。HBase 不可用/超时/空结果时回退本地；都没有返回 null（不抛、不卡死）。
     */
    public Map<String, Object> getImageBytes(String rowKey) throws IOException {
        if (rowKey == null || rowKey.trim().isEmpty()) {
            return null;
        }
        String key = rowKey.trim();
        long timeout = readTimeoutMs > 0 ? readTimeoutMs : 3000L;

        if (enabled && System.currentTimeMillis() >= hbaseSkipUntilMs) {
            try {
                Map<String, Object> fromHb = callWithTimeout(new Callable<Map<String, Object>>() {
                    @Override
                    public Map<String, Object> call() throws Exception {
                        return getFromHBase(key);
                    }
                }, timeout, "get rowKey=" + key);
                if (fromHb != null && fromHb.get("bytes") != null) {
                    byte[] bytes = (byte[]) fromHb.get("bytes");
                    log.info("[HBase读图] 成功 rowKey={} bytes={} mime={}",
                            key, Integer.valueOf(bytes.length), fromHb.get("mimeType"));
                    return fromHb;
                }
                log.warn("[HBase读图] 无可用字节，回退本地 rowKey={}", key);
            } catch (TimeoutException e) {
                markHbaseUnhealthy("timeout " + timeout + "ms");
                log.warn("[HBase读图] 超时，回退本地 rowKey={} timeoutMs={}", key, Long.valueOf(timeout));
            } catch (Exception e) {
                markHbaseUnhealthy(e.getMessage());
                log.warn("[HBase读图] 失败，回退本地 rowKey={} err={}", key, e.getMessage());
            }
        } else if (enabled && System.currentTimeMillis() < hbaseSkipUntilMs) {
            log.debug("[HBase读图] 冷却中，跳过 HBase rowKey={}", key);
        }

        Map<String, Object> local = getFromLocal(key);
        if (local != null && local.get("bytes") != null) {
            log.info("[HBase读图] 本地回退成功 rowKey={} bytes={}",
                    key, Integer.valueOf(((byte[]) local.get("bytes")).length));
        }
        return local;
    }

    private void markHbaseUnhealthy(String reason) {
        long until = System.currentTimeMillis() + (failCooldownMs > 0 ? failCooldownMs : 60000L);
        hbaseSkipUntilMs = until;
        // 连接可能已半死，丢掉后下次再试
        Connection old = connection;
        connection = null;
        if (old != null) {
            try {
                old.close();
            } catch (Exception ignore) {
                // ignore
            }
        }
        log.warn("[HBase读图] 进入冷却 {}ms reason={}", Long.valueOf(failCooldownMs), reason);
    }

    private <T> T callWithTimeout(Callable<T> task, long timeoutMs, String label)
            throws TimeoutException, Exception {
        Future<T> future = ioPool.submit(task);
        try {
            return future.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            future.cancel(true);
            throw e;
        } catch (ExecutionException e) {
            Throwable cause = e.getCause() == null ? e : e.getCause();
            if (cause instanceof Exception) {
                throw (Exception) cause;
            }
            throw new IOException(label + " failed: " + cause.getMessage(), cause);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            future.cancel(true);
            throw new IOException(label + " interrupted", e);
        }
    }

    private Map<String, Object> getFromLocal(String rowKey) throws IOException {
        Path dir = resolveLocalDir();
        String safe = rowKey.replace(":", "_");
        Path bin = dir.resolve(safe + ".bin");
        if (!Files.isRegularFile(bin)) {
            return null;
        }
        byte[] data = Files.readAllBytes(bin);
        String mime = "application/octet-stream";
        Path meta = dir.resolve(safe + ".meta.txt");
        if (Files.isRegularFile(meta)) {
            for (String line : Files.readAllLines(meta, StandardCharsets.UTF_8)) {
                if (line.startsWith("mime=")) {
                    String v = line.substring(5).trim();
                    if (!v.isEmpty()) {
                        mime = v;
                    }
                }
            }
        }
        Map<String, Object> out = new HashMap<String, Object>();
        out.put("bytes", data);
        out.put("mimeType", mime);
        return out;
    }

    /** getRowData 风格：Get → Cell offset/length → Map → 解析图片 */
    private Map<String, Object> getFromHBase(String rowKey) throws IOException {
        Table table = null;
        try {
            Connection conn = getConnection();
            String tableName = imageTable == null || imageTable.trim().isEmpty()
                    ? "collect_image_bytes" : imageTable.trim();
            table = conn.getTable(TableName.valueOf(tableName));
            Get get = new Get(Bytes.toBytes(rowKey));
            Result result = table.get(get);
            if (result == null || result.isEmpty()) {
                log.warn("[HBase读图] Result 为空 table={} rowKey={}", tableName, rowKey);
                return null;
            }
            Map<String, String> rowData = toRowDataMap(result);
            log.info("[HBase读图] 命中行 table={} rowKey={} qualifiers={}",
                    tableName, rowKey, rowData.keySet());
            Map<String, Object> parsed = parseRowDataMap(rowData);
            if (parsed != null) {
                return parsed;
            }
            return parseLegacyBinaryCells(result);
        } finally {
            if (table != null) {
                try {
                    table.close();
                } catch (IOException e) {
                    log.warn("[HBase读图] 关闭 table 失败: {}", e.getMessage());
                }
            }
        }
    }

    private Map<String, String> toRowDataMap(Result result) {
        Map<String, String> map = new LinkedHashMap<String, String>();
        if (result == null || result.isEmpty() || result.listCells() == null) {
            return map;
        }
        for (Cell cell : result.listCells()) {
            if (cell == null) {
                continue;
            }
            String family = Bytes.toString(
                    cell.getFamilyArray(), cell.getFamilyOffset(), cell.getFamilyLength());
            String qualifier = Bytes.toString(
                    cell.getQualifierArray(), cell.getQualifierOffset(), cell.getQualifierLength());
            String value = Bytes.toString(
                    cell.getValueArray(), cell.getValueOffset(), cell.getValueLength());
            if (qualifier != null) {
                map.put(qualifier, value);
            }
            if (family != null && qualifier != null) {
                map.put(family + ":" + qualifier, value);
            }
        }
        return map;
    }

    private Map<String, Object> parseRowDataMap(Map<String, String> rowData) {
        if (rowData == null || rowData.isEmpty()) {
            return null;
        }
        String[] preferredKeys = new String[] {
                "data", "base64_data", "base64", "content", "image", "value",
                "info:data", "info:base64_data", "cf:data", "cf:base64_data"
        };
        for (String key : preferredKeys) {
            Map<String, Object> parsed = decodeBase64Payload(rowData.get(key));
            if (parsed != null) {
                log.info("[HBase读图] 使用列 {} 解析成功", key);
                return parsed;
            }
        }
        for (Map.Entry<String, String> e : rowData.entrySet()) {
            String text = e.getValue();
            if (text == null || text.isEmpty()) {
                continue;
            }
            if (text.contains("base64_data") || text.startsWith("data:image")
                    || text.startsWith("{") || looksLikeBase64(text)) {
                Map<String, Object> parsed = decodeBase64Payload(text);
                if (parsed != null) {
                    log.info("[HBase读图] 使用列 {} 扫描解析成功", e.getKey());
                    return parsed;
                }
            }
        }
        return null;
    }

    private Map<String, Object> parseLegacyBinaryCells(Result result) {
        if (result == null || result.listCells() == null) {
            return null;
        }
        for (Cell cell : result.listCells()) {
            if (cell == null) {
                continue;
            }
            String qualifier = Bytes.toString(
                    cell.getQualifierArray(), cell.getQualifierOffset(), cell.getQualifierLength());
            if (qualifier == null) {
                continue;
            }
            String q = qualifier.toLowerCase();
            if (!"bytes".equals(q) && !"content".equals(q) && !"image".equals(q) && !"bin".equals(q)) {
                continue;
            }
            byte[] raw = Bytes.copy(
                    cell.getValueArray(), cell.getValueOffset(), cell.getValueLength());
            if (raw == null || raw.length < 24 || !looksLikeImageMagic(raw)) {
                continue;
            }
            String mime = "image/jpeg";
            if (raw[0] == (byte) 0x89 && raw[1] == 0x50) {
                mime = "image/png";
            } else if (raw[0] == 0x47 && raw[1] == 0x49) {
                mime = "image/gif";
            } else if (raw.length > 12 && raw[0] == 0x52 && raw[8] == 0x57) {
                mime = "image/webp";
            }
            Map<String, Object> out = new HashMap<String, Object>();
            out.put("bytes", raw);
            out.put("mimeType", mime);
            return out;
        }
        return null;
    }

    private static boolean looksLikeImageMagic(byte[] raw) {
        if (raw == null || raw.length < 3) {
            return false;
        }
        if ((raw[0] & 0xFF) == 0xFF && (raw[1] & 0xFF) == 0xD8) {
            return true;
        }
        if ((raw[0] & 0xFF) == 0x89 && raw[1] == 0x50 && raw[2] == 0x4E) {
            return true;
        }
        if (raw[0] == 0x47 && raw[1] == 0x49 && raw[2] == 0x46) {
            return true;
        }
        return raw.length > 12 && raw[0] == 0x52 && raw[1] == 0x49 && raw[2] == 0x46 && raw[3] == 0x46
                && raw[8] == 0x57 && raw[9] == 0x45 && raw[10] == 0x42 && raw[11] == 0x50;
    }

    private static boolean looksLikeBase64(String text) {
        if (text == null || text.length() < 64) {
            return false;
        }
        String t = text.trim();
        if (t.startsWith("{") || t.startsWith("data:")) {
            return false;
        }
        int n = Math.min(80, t.length());
        for (int i = 0; i < n; i++) {
            char c = t.charAt(i);
            if (!(Character.isLetterOrDigit(c) || c == '+' || c == '/' || c == '=' || c == '\n' || c == '\r')) {
                return false;
            }
        }
        return true;
    }

    private Map<String, Object> decodeBase64Payload(String text) {
        String raw = text == null ? "" : text.trim();
        if (raw.isEmpty()) {
            return null;
        }
        String base64Part = raw;
        String mime = "image/jpeg";
        if (raw.startsWith("{")) {
            try {
                JSONObject obj = JSON.parseObject(raw);
                if (obj == null) {
                    return null;
                }
                String candidate = firstNonBlank(
                        obj.getString("base64_data"),
                        obj.getString("base64Data"),
                        obj.getString("base64"),
                        obj.getString("data"),
                        obj.getString("content")
                );
                if (candidate == null) {
                    return null;
                }
                base64Part = candidate.trim();
            } catch (Exception e) {
                return null;
            }
        }
        if (base64Part.startsWith("data:")) {
            int comma = base64Part.indexOf(',');
            if (comma > 5) {
                String header = base64Part.substring(5, comma);
                int semi = header.indexOf(';');
                if (semi > 0) {
                    mime = header.substring(0, semi).trim();
                } else if (!header.trim().isEmpty()) {
                    mime = header.trim();
                }
                base64Part = base64Part.substring(comma + 1);
            }
        }
        try {
            base64Part = base64Part.replace("\r", "").replace("\n", "").replace(" ", "");
            byte[] bytes = Base64.getDecoder().decode(base64Part);
            if (bytes == null || bytes.length == 0) {
                return null;
            }
            Map<String, Object> out = new HashMap<String, Object>();
            out.put("bytes", bytes);
            out.put("mimeType", mime);
            return out;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private static String firstNonBlank(String... vals) {
        if (vals == null) {
            return null;
        }
        for (String v : vals) {
            if (v != null && !v.trim().isEmpty()) {
                return v;
            }
        }
        return null;
    }

    private Connection getConnection() throws IOException {
        if (connection != null && !connection.isClosed()) {
            return connection;
        }
        long waitMs = readTimeoutMs > 0 ? readTimeoutMs : 3000L;
        boolean locked;
        try {
            locked = connectLock.tryLock(waitMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("hbase_connect_interrupted", e);
        }
        if (!locked) {
            throw new IOException("hbase_connect_busy_or_timeout");
        }
        try {
            if (connection != null && !connection.isClosed()) {
                return connection;
            }
            Configuration conf = new Configuration();
            conf.set("hbase.zookeeper.quorum", zkQuorum);
            conf.set("hbase.zookeeper.property.clientPort", zkPort);
            conf.setInt("hbase.client.retries.number", 1);
            int to = (int) Math.min(waitMs, 3000L);
            conf.setInt("hbase.rpc.timeout", to);
            conf.setInt("hbase.client.operation.timeout", to);
            conf.setInt("hbase.client.meta.operation.timeout", to);
            conf.setInt("zookeeper.recovery.retry", 0);
            conf.setInt("zookeeper.session.timeout", 3000);
            connection = ConnectionFactory.createConnection(conf);
            log.info("[HBase读图] 连接已建立 quorum={} port={} table={} timeoutMs={}",
                    zkQuorum, zkPort, imageTable, Long.valueOf(waitMs));
            return connection;
        } finally {
            connectLock.unlock();
        }
    }

    private Path resolveLocalDir() {
        if (localDir != null && !localDir.trim().isEmpty()) {
            return Paths.get(localDir.trim());
        }
        return Paths.get(System.getProperty("user.dir")).resolve("data").resolve("image_bytes");
    }

    @PreDestroy
    public void destroy() {
        ioPool.shutdownNow();
        if (connection != null) {
            try {
                connection.close();
            } catch (IOException e) {
                log.warn("[HBase读图] 关闭连接失败: {}", e.getMessage());
            }
        }
    }
}
