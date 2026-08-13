package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.AgentL4DetailMapper;
import com.example.aw.entity.Result;
import org.elasticsearch.action.get.GetRequest;
import org.elasticsearch.action.get.GetResponse;
import org.elasticsearch.action.search.SearchRequest;
import org.elasticsearch.action.search.SearchResponse;
import org.elasticsearch.client.RequestOptions;
import org.elasticsearch.client.RestHighLevelClient;
import org.elasticsearch.index.query.QueryBuilders;
import org.elasticsearch.search.SearchHit;
import org.elasticsearch.search.builder.SearchSourceBuilder;
import org.elasticsearch.search.sort.SortOrder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import javax.annotation.Resource;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

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

            List<Map<String, Object>> all = new ArrayList<>();
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
            Map<String, List<Map<String, Object>>> byParent = new HashMap<>();
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

            List<Map<String, Object>> children = new ArrayList<>();
            for (SearchHit hit : response.getHits().getHits()) {
                Map<String, Object> map = JSON.parseObject(hit.getSourceAsString(), Map.class);
                map.put("esId", hit.getId());
                children.add(map);
            }

            Map<String, Object> records = new LinkedHashMap<>();
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
            String nodeId = esId.trim();
            Map<String, Object> row = agentL4DetailMapper.selectByNodeId(nodeId);
            if (row == null || row.isEmpty()) {
                return new Result(404, "未查询到L4详情", 0, 0, null);
            }
            // image_examples / image_row_keys 可能以 JSON 字符串返回，统一解析为数组
            parseJsonArrayField(row, "imageExamples", "image_examples");
            parseJsonArrayField(row, "imageRowKeys", "image_row_keys");
            row.put("esId", nodeId);
            return new Result(200, "查询成功", 1, 1, row);
        } catch (Exception e) {
            logger.error("getL4DetailByEsId error esId={}", esId, e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /** 将 Map 中可能为字符串的 JSON 数组字段解析为对象 */
    private void parseJsonArrayField(Map<String, Object> row, String camelKey, String snakeKey) {
        Object val = row.get(camelKey);
        String useKey = camelKey;
        if (val == null) {
            val = row.get(snakeKey);
            useKey = snakeKey;
        }
        if (!(val instanceof String)) {
            return;
        }
        String raw = ((String) val).trim();
        if (raw.isEmpty()) {
            return;
        }
        row.put(useKey, JSON.parse(raw));
    }

    /**
     * 首页大屏看板：汇总数字 + 近7天图表，一次性返回。
     * <p>
     * 数据来自 ES 索引 {@code hermes_xa_homepage_stats}：
     * <ul>
     *   <li>{@code id=summary} —— 顶部数字卡片</li>
     *   <li>{@code type=daily} —— 近7天曲线/柱状图序列</li>
     * </ul>
     * <p>
     * <b>records 字段（供前端渲染）：</b>
     * <ul>
     *   <li>{@code todayUsage} —— 今日用量（数字，当日累计）</li>
     *   <li>{@code historyTaskTotal} —— 历史任务总数（数字）</li>
     *   <li>{@code historyTaskCompletionRate} —— 历史任务完成率（整数百分比，如 98 表示 98%）</li>
     *   <li>{@code agentTotal} —— Agent 总数（数字，写死）</li>
     *   <li>{@code agentOnline} —— Agent 在线数量（数字，写死）</li>
     *   <li>{@code agentOnlineRate} —— Agent 在线率（整数百分比，如 97 表示 97%）</li>
     *   <li>{@code charts} —— 近7天图表（各数组下标与 dates 一一对应，已按日期升序）
     *     <ul>
     *       <li>{@code dates} —— X 轴日期，格式 yyyy-MM-dd</li>
     *       <li>{@code verify} —— 账号核查类调用量；同时用于「调研用量趋势」的核查类曲线（同一份数据）</li>
     *       <li>{@code report} —— 写报类调用量；用于「调研用量趋势」的写报类曲线</li>
     *       <li>{@code social} —— 社交类调用统计（曲线图）</li>
     *       <li>{@code business} —— 业务专属类调用统计（曲线图）</li>
     *     </ul>
     *   </li>
     * </ul>
     */
    public Result getDashboard() {
        try {
            // ----------------------------
            // 1. 读汇总文档 summary（顶部数字）
            // ----------------------------
            GetRequest getRequest = new GetRequest(INDEX_STATS, "summary");
            GetResponse getResponse = restHighLevelClient5602.get(getRequest, RequestOptions.DEFAULT);
            if (!getResponse.isExists()) {
                return new Result(404, "未找到首页汇总数据", 0, 0, null);
            }
            Map<String, Object> summary = JSON.parseObject(getResponse.getSourceAsString(), Map.class);

            // ----------------------------
            // 2. 查近7天 daily（先按日期降序取7条，再反转为升序给前端画图）
            // ----------------------------
            SearchRequest searchRequest = new SearchRequest(INDEX_STATS);
            SearchSourceBuilder sourceBuilder = new SearchSourceBuilder();
            sourceBuilder.query(QueryBuilders.termQuery("type", "daily"));
            sourceBuilder.size(7);
            sourceBuilder.sort("date", SortOrder.DESC);
            sourceBuilder.fetchSource(true);
            searchRequest.source(sourceBuilder);

            logger.info("ES DSL => {}", sourceBuilder.toString());

            SearchResponse response = restHighLevelClient5602.search(searchRequest, RequestOptions.DEFAULT);

            List<Map<String, Object>> dailyList = new ArrayList<>();
            for (SearchHit hit : response.getHits().getHits()) {
                Map<String, Object> map = JSON.parseObject(hit.getSourceAsString(), Map.class);
                dailyList.add(map);
            }
            // 升序：从旧到新，方便折线/柱状从左到右渲染
            Collections.reverse(dailyList);

            // ----------------------------
            // 3. 拆成并行数组，前端直接绑定 series
            // ----------------------------
            List<String> dates = new ArrayList<>();
            List<Integer> verify = new ArrayList<>();
            List<Integer> report = new ArrayList<>();
            List<Integer> social = new ArrayList<>();
            List<Integer> business = new ArrayList<>();
            for (Map<String, Object> d : dailyList) {
                dates.add(d.get("date") == null ? "" : String.valueOf(d.get("date")));
                // 账号核查柱状图 = 调研趋势「核查类」曲线，共用 verify
                verify.add(toIntValue(d.get("verifyCount")));
                // 调研趋势「写报类」曲线
                report.add(toIntValue(d.get("reportCount")));
                // 社交类曲线
                social.add(toIntValue(d.get("socialCount")));
                // 业务专属类曲线
                business.add(toIntValue(d.get("businessCount")));
            }

            Map<String, Object> charts = new LinkedHashMap<>();
            charts.put("dates", dates);
            charts.put("verify", verify);
            charts.put("report", report);
            charts.put("social", social);
            charts.put("business", business);

            // ----------------------------
            // 4. 组装返回（与前端字段约定一致）
            // ----------------------------
            Map<String, Object> records = new LinkedHashMap<>();
            // 今日用量
            records.put("todayUsage", summary.get("todayUsage"));
            // 历史任务总数
            records.put("historyTaskTotal", summary.get("historyTaskTotal"));
            // 历史任务完成率（% 整数）
            records.put("historyTaskCompletionRate", summary.get("historyTaskCompletionRate"));
            // Agent 总数 / 在线数 / 在线率（% 整数）
            records.put("agentTotal", summary.get("agentTotal"));
            records.put("agentOnline", summary.get("agentOnline"));
            records.put("agentOnlineRate", summary.get("agentOnlineRate"));
            // 近7天图表
            records.put("charts", charts);

            return new Result(200, "查询成功", 1, 1, records);

        } catch (Exception e) {
            logger.error("getDashboard error", e);
            return new Result(400, "查询失败", 0, 0, null);
        }
    }

    /** Object 转 int，非法值按 0 */
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
        Map<String, Object> node = new LinkedHashMap<>(source);
        String id = String.valueOf(source.get("id"));

        // displayCount
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

        // 只取 show=true，按 weight 降序，截到 displayCount
        List<Map<String, Object>> visible = new ArrayList<>();
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

        List<Map<String, Object>> children = new ArrayList<>();
        for (Map<String, Object> child : visible) {
            children.add(buildTree(child, byParent));
        }
        node.put("children", children);
        return node;
    }
}
