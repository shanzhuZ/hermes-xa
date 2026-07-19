package com.example.aw.hbase;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.hbase.Cell;
import org.apache.hadoop.hbase.CellUtil;
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
import java.util.Map;

/**
 * 图片原图 HBase 读取客户端（ZK 原生）。
 * <p>
 * 写入由 Python 调现成 HTTP 接口 insertHbaseData 完成，落库结构对齐历史写法：
 * 列族 info，单元格为 JSON：{"image_url":"...","base64_data":"data:image/xxx;base64,..."}
 * <p>
 * 读侧兼容：
 * 1) info:data / info:base64_data / 列族内任意含 base64_data 的 JSON
 * 2) 旧格式 cf:bytes + cf:mime
 * 3) HBase 关闭时读本地回退目录
 */
@Component
public class HBaseImageClient {

    private static final Logger log = LoggerFactory.getLogger(HBaseImageClient.class);

    private static final byte[] CF_INFO = Bytes.toBytes("info");
    private static final byte[] CF_LEGACY = Bytes.toBytes("cf");
    private static final byte[] COL_DATA = Bytes.toBytes("data");
    private static final byte[] COL_BASE64 = Bytes.toBytes("base64_data");
    private static final byte[] COL_BYTES = Bytes.toBytes("bytes");
    private static final byte[] COL_MIME = Bytes.toBytes("mime");

    @Value("${hermes.hbase.enabled:false}")
    private boolean enabled;

    /** 必须指向 insertHbaseData 实际写入的集群 */
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

    private volatile Connection connection;

    /**
     * 按 RowKey 读取原图。
     *
     * @param rowKey MySQL collect_images.hbase_row_key
     * @return 含 bytes、mimeType；不存在返回 null
     */
    public Map<String, Object> getImageBytes(String rowKey) throws IOException {
        if (rowKey == null || rowKey.trim().isEmpty()) {
            return null;
        }
        if (enabled) {
            return getFromHBase(rowKey.trim());
        }
        return getFromLocal(rowKey.trim());
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

    private Map<String, Object> getFromHBase(String rowKey) throws IOException {
        Table table = null;
        try {
            Connection conn = getConnection();
            table = conn.getTable(TableName.valueOf(imageTable));
            Get get = new Get(Bytes.toBytes(rowKey));
            Result result = table.get(get);
            if (result == null || result.isEmpty()) {
                return null;
            }

            Map<String, Object> parsed = parseInsertApiFormat(result);
            if (parsed != null) {
                return parsed;
            }
            return parseLegacyFormat(result);
        } finally {
            if (table != null) {
                try {
                    table.close();
                } catch (IOException e) {
                    log.warn("关闭 HBase table 失败: {}", e.getMessage());
                }
            }
        }
    }

    /**
     * 解析 insertHbaseData 写入格式：列族 info 内 JSON / base64_data。
     */
    private Map<String, Object> parseInsertApiFormat(Result result) {
        byte[] family = Bytes.toBytes(columnFamily == null || columnFamily.trim().isEmpty()
                ? "info" : columnFamily.trim());

        String text = firstNonEmpty(
                bytesToString(result.getValue(family, COL_DATA)),
                bytesToString(result.getValue(family, COL_BASE64)),
                bytesToString(result.getValue(CF_INFO, COL_DATA)),
                bytesToString(result.getValue(CF_INFO, COL_BASE64))
        );
        if (text == null) {
            // 扫描列族内所有列，找含 base64_data 的 JSON 或直接 data URI
            for (Cell cell : result.listCells()) {
                if (cell == null) {
                    continue;
                }
                byte[] fam = CellUtil.cloneFamily(cell);
                if (!Bytes.equals(fam, family) && !Bytes.equals(fam, CF_INFO)) {
                    continue;
                }
                String cellText = bytesToString(CellUtil.cloneValue(cell));
                if (cellText == null) {
                    continue;
                }
                if (cellText.contains("base64_data") || cellText.startsWith("data:image")) {
                    text = cellText;
                    break;
                }
            }
        }
        if (text == null) {
            return null;
        }
        return decodeBase64Payload(text);
    }

    /** 兼容早期 cf:bytes / cf:mime */
    private Map<String, Object> parseLegacyFormat(Result result) {
        byte[] data = result.getValue(CF_LEGACY, COL_BYTES);
        if (data == null || data.length == 0) {
            return null;
        }
        String mime = bytesToString(result.getValue(CF_LEGACY, COL_MIME));
        if (mime == null || mime.trim().isEmpty()) {
            mime = "application/octet-stream";
        }
        Map<String, Object> out = new HashMap<String, Object>();
        out.put("bytes", data);
        out.put("mimeType", mime.trim());
        return out;
    }

    /**
     * 支持：
     * 1) {"image_url":"...","base64_data":"data:image/jpeg;base64,xxxx"}
     * 2) data:image/jpeg;base64,xxxx
     * 3) 纯 base64
     */
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
                if (obj != null && obj.getString("base64_data") != null) {
                    base64Part = obj.getString("base64_data").trim();
                }
            } catch (Exception e) {
                log.warn("解析 HBase JSON 单元格失败: {}", e.getMessage());
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
            byte[] bytes = Base64.getDecoder().decode(base64Part);
            if (bytes == null || bytes.length == 0) {
                return null;
            }
            Map<String, Object> out = new HashMap<String, Object>();
            out.put("bytes", bytes);
            out.put("mimeType", mime);
            return out;
        } catch (IllegalArgumentException e) {
            log.warn("Base64 解码失败: {}", e.getMessage());
            return null;
        }
    }

    private static String bytesToString(byte[] raw) {
        if (raw == null || raw.length == 0) {
            return null;
        }
        String text = new String(raw, StandardCharsets.UTF_8).trim();
        return text.isEmpty() ? null : text;
    }

    private static String firstNonEmpty(String... vals) {
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
        synchronized (this) {
            if (connection != null && !connection.isClosed()) {
                return connection;
            }
            Configuration conf = new Configuration();
            conf.set("hbase.zookeeper.quorum", zkQuorum);
            conf.set("hbase.zookeeper.property.clientPort", zkPort);
            connection = ConnectionFactory.createConnection(conf);
            log.info("HBase 连接已建立 quorum={} port={} table={} cf={}",
                    zkQuorum, zkPort, imageTable, columnFamily);
            return connection;
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
        if (connection != null) {
            try {
                connection.close();
            } catch (IOException e) {
                log.warn("关闭 HBase 连接失败: {}", e.getMessage());
            }
        }
    }
}
