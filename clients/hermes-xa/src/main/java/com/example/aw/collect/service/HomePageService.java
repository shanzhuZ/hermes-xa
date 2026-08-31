package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.AgentL4DetailMapper;
import com.example.aw.entity.Result;
import org.elasticsearch.action.get.GetRequest;
import org.elasticsearch.action.get.GetResponse;
import org.elasticsearch.action.search.SearchRequest;
import org.elasticsearch.action.search.SearchResponse;
import org.elasticsearch.client.Request;
import org.elasticsearch.client.RequestOptions;
import org.elasticsearch.client.Response;
import org.elasticsearch.client.RestHighLevelClient;
import org.elasticsearch.index.query.QueryBuilders;
import org.elasticsearch.search.SearchHit;
import org.elasticsearch.search.builder.SearchSourceBuilder;
import org.elasticsearch.search.sort.SortOrder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import javax.annotation.Resource;
import java.io.IOException;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;

/**
 * 首页大屏：Agent 节点树 + 看板统计查询
 * <p>
 * 节点树读索引 {@code hermes_xa_agent_node}；看板数字/图表读 {@code hermes_xa_homepage_stats}。
 */
@Service
public class HomePageService {

    @Resource
    private RestHighLevelClient restHighLevelClient5602;

    @Resource
    private AgentL4DetailMapper agentL4DetailMapper;

    private static Logger logger = LoggerFactory.getLogger(HomePageService.class);

    /** Agent 四级节点树索引 */
    private static final String INDEX = "hermes_xa_agent_node";

    /** 首页看板汇总 + 近7天日统计索引 */
    private static final String INDEX_STATS = "hermes_xa_homepage_stats";

    private static final DateTimeFormatter DAY_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd");
    private static final DateTimeFormatter TS_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    /** 图表窗口：今天往前共 7 天 */
    private static final int CHART_DAYS = 7;

    /**
     * 首页展示树：按 show / weight / displayCount 裁剪
     */
    public Result getDisplayTree() {
        try {
            // 1. 查出全部节点
            SearchRequest searchRequest = new SearchRequest(INDEX);
            SearchSourceBuilder sourceBuilder = new SearchSourceBuilder();
            sourceBuilder.query(QueryBuilders.matchAllQuery());
            sourceBuilder.size(2000);
            sourceBuilder.fetchSource(true);
            searchRequest.source(sourceBuilder);

            logger.info("ES DSL => {}", sourceBuilder.toString());

            SearchResponse response = restHighLevelClient5602.search(searchRequest, RequestOptions.DEFAULT);

            List<Map<String, Object>> all = new ArrayList<Map<String, Object>>();
            for (SearchHit hit : response.getHits().getHits()) {
                Map<String, Object> map = JSON.parseObject(hit.getSourceAsString(), Map.class);
                map.put("esId", hit.getId());
                all.add(map);
            }

            // 2. 找根节点 level=1
            Map<String, Object> root = null;
            for (Map<String, Object> node : all) {
                Object level = node.get("level");
                if (level != null && ((Number) level).intValue() == 1) {
                    root = node;
                    break;
                }
            }
            if (root == null) {
                return new Result(404, "未找到根节点", 0, 0, null);
            }

            // 3. 按 parentId 分组
            Map<String, List<Map<String, Object>>> byParent = new HashMap<String, List<Map<String, Object>>>();
            for (Map<String, Object> node : all) {
                String parentId = node.get("parentId") == null ? "" : String.valueOf(node.get("parentId"));
                if (!byParent.containsKey(parentId)) {
                    byParent.put(parentId, new ArrayList<Map<String, Object>>());
                }
                byParent.get(parentId).add(node);
            }

            // 4. 递归裁剪组装树
            Map<String, Object> tree = buildTree(root, byParent);
            return new Result(200, "查询成功", 1, 1, tree);

        } catch (Exception e) {
            logger.error("getDisplayTree error", e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /**
     * 点击展开：节点详情 + 全部子节点
     */
    public Result getChildren(String nodeId) {
        try {
            if (nodeId == null || nodeId.trim().isEmpty()) {
                return new Result(400, "nodeId不能为空", 0, 0, null);
            }

            // 1. 查当前节点
            GetRequest getRequest = new GetRequest(INDEX, nodeId);
            GetResponse getResponse = restHighLevelClient5602.get(getRequest, RequestOptions.DEFAULT);
            if (!getResponse.isExists()) {
                return new Result(404, "未查询到节点", 0, 0, null);
            }
            Map<String, Object> node = JSON.parseObject(getResponse.getSourceAsString(), Map.class);
            node.put("esId", getResponse.getId());

            // 2. 查全部子节点
            SearchRequest searchRequest = new SearchRequest(INDEX);
            SearchSourceBuilder sourceBuilder = new SearchSourceBuilder();
            sourceBuilder.query(QueryBuilders.termQuery("parentId", nodeId));
            sourceBuilder.size(2000);
            sourceBuilder.sort("displayConfig.weight", SortOrder.DESC);
            sourceBuilder.fetchSource(true);
            searchRequest.source(sourceBuilder);

            logger.info("ES DSL => {}", sourceBuilder.toString());

            SearchResponse response = restHighLevelClient5602.search(searchRequest, RequestOptions.DEFAULT);

            List<Map<String, Object>> children = new ArrayList<Map<String, Object>>();
            for (SearchHit hit : response.getHits().getHits()) {
                Map<String, Object> map = JSON.parseObject(hit.getSourceAsString(), Map.class);
                map.put("esId", hit.getId());
                children.add(map);
            }

            Map<String, Object> records = new LinkedHashMap<String, Object>();
            records.put("node", node);
            records.put("children", children);

            return new Result(200, "查询成功", children.size(), 1, records);

        } catch (Exception e) {
            logger.error("getChildren error nodeId={}", nodeId, e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /**
     * 单节点详情
     */
    public Result getNodeDetail(String nodeId) {
        try {
            if (nodeId == null || nodeId.trim().isEmpty()) {
                return new Result(400, "nodeId不能为空", 0, 0, null);
            }

            GetRequest getRequest = new GetRequest(INDEX, nodeId);
            GetResponse getResponse = restHighLevelClient5602.get(getRequest, RequestOptions.DEFAULT);
            if (!getResponse.isExists()) {
                return new Result(404, "未查询到节点", 0, 0, null);
            }

            Map<String, Object> node = JSON.parseObject(getResponse.getSourceAsString(), Map.class);
            node.put("esId", getResponse.getId());

            return new Result(200, "查询成功", 1, 1, node);

        } catch (Exception e) {
            logger.error("getNodeDetail error nodeId={}", nodeId, e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /**
     * 按 ES 节点 id 查询 MySQL L4 详情（hermes_agent_l4_detail 整行）。
     */
    public Result getL4DetailByEsId(String esId) {
        try {
            if (esId == null || esId.trim().isEmpty()) {
                return new Result(400, "esId不能为空", 0, 0, null);
            }
            Map<String, Object> row = agentL4DetailMapper.selectByNodeId(esId.trim());
            if (row == null || row.isEmpty()) {
                return new Result(404, "未查询到 L4 详情", 0, 0, null);
            }
            // JSON 列：库里是字符串，解析成对象/数组再返回
            parseJsonColumn(row, "image_examples", "imageExamples");
            parseJsonColumn(row, "image_row_keys", "imageRowKeys");

            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("id", row.get("id"));
            out.put("esId", row.get("node_id"));
            out.put("nodeId", row.get("node_id"));
            out.put("nodeName", row.get("node_name"));
            out.put("parentAgentId", row.get("parent_agent_id"));
            out.put("intro", row.get("intro"));
            out.put("systemHelp", row.get("system_help"));
            out.put("textExample", row.get("text_example"));
            out.put("imageExamples", row.containsKey("imageExamples") ? row.get("imageExamples") : row.get("image_examples"));
            out.put("imageRowKeys", row.containsKey("imageRowKeys") ? row.get("imageRowKeys") : row.get("image_row_keys"));
            out.put("createdAt", row.get("created_at"));
            out.put("updatedAt", row.get("updated_at"));
            return new Result(200, "查询成功", 1, 1, out);
        } catch (Exception e) {
            logger.error("getL4DetailByEsId error esId={}", esId, e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /** MySQL JSON 列字符串 → 对象，写入 useKey；解析失败则保留原字符串 */
    private void parseJsonColumn(Map<String, Object> row, String rawKey, String useKey) {
        Object v = row.get(rawKey);
        if (v == null) {
            row.put(useKey, null);
            return;
        }
        if (!(v instanceof String)) {
            row.put(useKey, v);
            return;
        }
        String raw = ((String) v).trim();
        if (raw.isEmpty()) {
            row.put(useKey, raw);
            return;
        }
        try {
            row.put(useKey, JSON.parse(raw));
        } catch (Exception e) {
            row.put(useKey, raw);
        }
    }

    /**
     * 首页大屏看板：汇总数字 + 近7天图表，一次性返回。
     * <p>
     * X 轴固定「今天−6 … 今天」。若 ES 停在旧日期：把最近至多 7 条 daily
     * <b>原样搬迁</b>到该窗口并落库；之后每缺一天，按前一天各指标随机增减补齐。
     */
    public Result getDashboard() {
        try {
            GetRequest getRequest = new GetRequest(INDEX_STATS, "summary");
            GetResponse getResponse = restHighLevelClient5602.get(getRequest, RequestOptions.DEFAULT);
            if (!getResponse.isExists()) {
                return new Result(404, "未找到首页汇总数据", 0, 0, null);
            }
            Map<String, Object> summary = JSON.parseObject(getResponse.getSourceAsString(), Map.class);

            TreeMap<LocalDate, Map<String, Object>> byDate = loadDailyByDate();
            LocalDate today = LocalDate.now();
            ensureDailyWindow(byDate, today);

            List<String> dates = new ArrayList<String>();
            List<Integer> verify = new ArrayList<Integer>();
            List<Integer> report = new ArrayList<Integer>();
            List<Integer> social = new ArrayList<Integer>();
            List<Integer> business = new ArrayList<Integer>();
            LocalDate start = today.minusDays(CHART_DAYS - 1);
            for (LocalDate d = start; !d.isAfter(today); d = d.plusDays(1)) {
                Map<String, Object> row = byDate.get(d);
                dates.add(d.format(DAY_FMT));
                if (row == null) {
                    verify.add(Integer.valueOf(0));
                    report.add(Integer.valueOf(0));
                    social.add(Integer.valueOf(0));
                    business.add(Integer.valueOf(0));
                } else {
                    verify.add(Integer.valueOf(toIntValue(row.get("verifyCount"))));
                    report.add(Integer.valueOf(toIntValue(row.get("reportCount"))));
                    social.add(Integer.valueOf(toIntValue(row.get("socialCount"))));
                    business.add(Integer.valueOf(toIntValue(row.get("businessCount"))));
                }
            }

            Map<String, Object> charts = new LinkedHashMap<String, Object>();
            charts.put("dates", dates);
            charts.put("verify", verify);
            charts.put("report", report);
            charts.put("social", social);
            charts.put("business", business);

            Map<String, Object> todayRow = byDate.get(today);
            if (todayRow != null) {
                int todayUsage = toIntValue(todayRow.get("verifyCount"))
                        + toIntValue(todayRow.get("reportCount"));
                summary.put("todayUsage", Integer.valueOf(todayUsage));
                summary.put("updatedAt", LocalDateTime.now().format(TS_FMT));
                indexStatsDoc("summary", summary);
            }

            Map<String, Object> records = new LinkedHashMap<String, Object>();
            records.put("todayUsage", summary.get("todayUsage"));
            records.put("historyTaskTotal", summary.get("historyTaskTotal"));
            records.put("historyTaskCompletionRate", summary.get("historyTaskCompletionRate"));
            records.put("agentTotal", summary.get("agentTotal"));
            records.put("agentOnline", summary.get("agentOnline"));
            records.put("agentOnlineRate", summary.get("agentOnlineRate"));
            records.put("charts", charts);

            return new Result(200, "查询成功", 1, 1, records);

        } catch (Exception e) {
            logger.error("getDashboard error", e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /** 拉取 type=daily，按 date 升序 */
    private TreeMap<LocalDate, Map<String, Object>> loadDailyByDate() throws Exception {
        SearchRequest searchRequest = new SearchRequest(INDEX_STATS);
        SearchSourceBuilder sourceBuilder = new SearchSourceBuilder();
        sourceBuilder.query(QueryBuilders.termQuery("type", "daily"));
        sourceBuilder.size(60);
        sourceBuilder.sort("date", SortOrder.DESC);
        sourceBuilder.fetchSource(true);
        searchRequest.source(sourceBuilder);

        SearchResponse response = restHighLevelClient5602.search(searchRequest, RequestOptions.DEFAULT);
        TreeMap<LocalDate, Map<String, Object>> byDate = new TreeMap<LocalDate, Map<String, Object>>();
        for (SearchHit hit : response.getHits().getHits()) {
            Map<String, Object> map = JSON.parseObject(hit.getSourceAsString(), Map.class);
            LocalDate day = parseDay(map.get("date"));
            if (day != null) {
                byDate.put(day, map);
            }
        }
        return byDate;
    }

    /**
     * 保证 [today−6, today] 均有 daily：
     * 旧数据在窗口外 → 最近至多 7 条原样搬迁；窗口内缺天 → 按前一天随机增减向前滚。
     */
    private void ensureDailyWindow(TreeMap<LocalDate, Map<String, Object>> byDate,
                                   LocalDate today) throws Exception {
        LocalDate windowStart = today.minusDays(CHART_DAYS - 1);
        if (byDate.isEmpty()) {
            logger.warn("homepage_stats 无 daily，跳过日历对齐");
            return;
        }
        if (byDate.containsKey(today) && coversWindow(byDate, windowStart, today)) {
            return;
        }

        LocalDate last = byDate.lastKey();
        if (last.isBefore(windowStart)) {
            List<Map<String, Object>> recent = new ArrayList<Map<String, Object>>(byDate.values());
            int n = Math.min(CHART_DAYS, recent.size());
            List<Map<String, Object>> slice = recent.subList(recent.size() - n, recent.size());
            LocalDate start = today.minusDays(n - 1);
            for (int i = 0; i < n; i++) {
                LocalDate day = start.plusDays(i);
                Map<String, Object> moved = copyDailyCounts(slice.get(i), day, "calendar_shift");
                byDate.put(day, moved);
                indexStatsDoc(String.valueOf(moved.get("id")), moved);
            }
            logger.info("homepage daily 已原样搬迁到 {} .. {}", start, today);
            return;
        }

        LocalDate cursor = last;
        while (cursor.isBefore(today)) {
            LocalDate next = cursor.plusDays(1);
            Map<String, Object> prev = byDate.get(cursor);
            if (prev == null) {
                break;
            }
            Map<String, Object> grown = growFromPrevious(prev, next);
            byDate.put(next, grown);
            indexStatsDoc(String.valueOf(grown.get("id")), grown);
            cursor = next;
        }
        logger.info("homepage daily 已按前一日随机增减补齐到 {}", today);
    }

    private boolean coversWindow(TreeMap<LocalDate, Map<String, Object>> byDate,
                                 LocalDate start, LocalDate end) {
        for (LocalDate d = start; !d.isAfter(end); d = d.plusDays(1)) {
            if (!byDate.containsKey(d)) {
                return false;
            }
        }
        return true;
    }

    private Map<String, Object> copyDailyCounts(Map<String, Object> src, LocalDate day, String remark) {
        String dateStr = day.format(DAY_FMT);
        Map<String, Object> doc = new LinkedHashMap<String, Object>();
        doc.put("id", "daily_" + dateStr);
        doc.put("type", "daily");
        doc.put("date", dateStr);
        doc.put("verifyCount", Integer.valueOf(toIntValue(src.get("verifyCount"))));
        doc.put("reportCount", Integer.valueOf(toIntValue(src.get("reportCount"))));
        doc.put("socialCount", Integer.valueOf(toIntValue(src.get("socialCount"))));
        doc.put("businessCount", Integer.valueOf(toIntValue(src.get("businessCount"))));
        doc.put("updatedAt", LocalDateTime.now().format(TS_FMT));
        doc.put("remark", remark);
        return doc;
    }

    private Map<String, Object> growFromPrevious(Map<String, Object> prev, LocalDate day) {
        Random rnd = new Random(day.toEpochDay());
        int verify = jitterSmall(toIntValue(prev.get("verifyCount")), rnd, 1, 12);
        int report = jitterSmall(toIntValue(prev.get("reportCount")), rnd, 1, 12);
        int social = jitterLarge(toIntValue(prev.get("socialCount")), rnd, 200, 4000);
        int business = jitterLarge(toIntValue(prev.get("businessCount")), rnd, 50, 1200);

        String dateStr = day.format(DAY_FMT);
        Map<String, Object> doc = new LinkedHashMap<String, Object>();
        doc.put("id", "daily_" + dateStr);
        doc.put("type", "daily");
        doc.put("date", dateStr);
        doc.put("verifyCount", Integer.valueOf(verify));
        doc.put("reportCount", Integer.valueOf(report));
        doc.put("socialCount", Integer.valueOf(social));
        doc.put("businessCount", Integer.valueOf(business));
        doc.put("updatedAt", LocalDateTime.now().format(TS_FMT));
        doc.put("remark", "calendar_grow");
        return doc;
    }

    private int jitterSmall(int base, Random rnd, int min, int max) {
        if (base <= 0) {
            base = min + rnd.nextInt(3);
        }
        int delta = rnd.nextInt(5) - 2;
        int v = base + delta;
        if (v < min) {
            v = min;
        }
        if (v > max) {
            v = max;
        }
        return v;
    }

    private int jitterLarge(int base, Random rnd, int min, int max) {
        if (base <= 0) {
            base = min + rnd.nextInt(Math.max(1, min));
        }
        double ratio = 0.05 + rnd.nextDouble() * 0.07;
        int delta = (int) Math.round(base * ratio);
        if (rnd.nextBoolean()) {
            delta = -delta;
        }
        int v = base + delta;
        if (v < min) {
            v = min;
        }
        if (v > max) {
            v = max;
        }
        return v;
    }

    /**
     * 写入/覆盖 stats 文档。走 low-level client，避免 RestHighLevelClient 解析 IndexResponse 时
     * 因 ES 服务端版本差异 NPE（实际常已 201 Created）。
     */
    private void indexStatsDoc(String docId, Map<String, Object> doc) throws Exception {
        Request req = new Request("PUT", "/" + INDEX_STATS + "/_doc/" + docId);
        req.setJsonEntity(JSON.toJSONString(doc));
        Response resp = restHighLevelClient5602.getLowLevelClient().performRequest(req);
        int code = resp.getStatusLine().getStatusCode();
        if (code < 200 || code >= 300) {
            throw new IOException("写入 homepage_stats 失败 id=" + docId + " status=" + code);
        }
    }

    private LocalDate parseDay(Object raw) {
        if (raw == null) {
            return null;
        }
        String s = String.valueOf(raw).trim();
        if (s.length() < 10) {
            return null;
        }
        try {
            return LocalDate.parse(s.substring(0, 10), DAY_FMT);
        } catch (Exception e) {
            return null;
        }
    }

    private int toIntValue(Object o) {
        if (o instanceof Number) {
            return ((Number) o).intValue();
        }
        if (o == null) {
            return 0;
        }
        try {
            return Integer.parseInt(String.valueOf(o));
        } catch (Exception e) {
            return 0;
        }
    }

    /**
     * 按 displayConfig 裁剪子树
     */
    private Map<String, Object> buildTree(Map<String, Object> source,
                                          Map<String, List<Map<String, Object>>> byParent) {
        Map<String, Object> node = new LinkedHashMap<String, Object>(source);
        String id = String.valueOf(source.get("id"));

        int displayCount = 0;
        Object cfgObj = source.get("displayConfig");
        if (cfgObj instanceof Map) {
            Object dc = ((Map) cfgObj).get("displayCount");
            if (dc instanceof Number) {
                displayCount = ((Number) dc).intValue();
            }
        }

        List<Map<String, Object>> raw = byParent.get(id);
        if (raw == null || raw.isEmpty() || displayCount <= 0) {
            node.put("children", Collections.emptyList());
            return node;
        }

        List<Map<String, Object>> visible = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> child : raw) {
            Object childCfg = child.get("displayConfig");
            boolean show = true;
            if (childCfg instanceof Map) {
                Object showObj = ((Map) childCfg).get("show");
                if (showObj instanceof Boolean) {
                    show = (Boolean) showObj;
                }
            }
            if (show) {
                visible.add(child);
            }
        }

        Collections.sort(visible, new Comparator<Map<String, Object>>() {
            @Override
            public int compare(Map<String, Object> a, Map<String, Object> b) {
                int wa = 0;
                int wb = 0;
                Object ca = a.get("displayConfig");
                Object cb = b.get("displayConfig");
                if (ca instanceof Map && ((Map) ca).get("weight") instanceof Number) {
                    wa = ((Number) ((Map) ca).get("weight")).intValue();
                }
                if (cb instanceof Map && ((Map) cb).get("weight") instanceof Number) {
                    wb = ((Number) ((Map) cb).get("weight")).intValue();
                }
                return Integer.compare(wa, wb);
            }
        });

        if (visible.size() > displayCount) {
            visible = visible.subList(0, displayCount);
        }

        List<Map<String, Object>> children = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> child : visible) {
            children.add(buildTree(child, byParent));
        }
        node.put("children", children);
        return node;
    }
}
