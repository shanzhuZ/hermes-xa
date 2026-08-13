package com.example.aw.collect.controller;

import com.example.aw.collect.service.HomePageService;
import com.example.aw.entity.Result;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import javax.annotation.Resource;

/**
 * 首页大屏接口。
 * <p>
 * 前缀 {@code /api/agent}：
 * <ul>
 *   <li>节点树：{@code /tree/display}、{@code /node/{id}/children}</li>
 *   <li>L4 详情：{@code /l4/detail?esId=}</li>
 *   <li>看板统计：{@code /homepage/dashboard}</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/agent")
public class HomePageController {

    @Resource
    private HomePageService homePageService;

    /**
     * 首页展示树（按 show / weight / displayCount 裁剪）
     */
    @GetMapping("/tree/display")
    public Result displayTree() {
        return homePageService.getDisplayTree();
    }

    /**
     * 点击展开：节点详情 + 全部直接子节点
     */
    @GetMapping("/node/{id}/children")
    public Result children(@PathVariable("id") String id) {
        return homePageService.getChildren(id);
    }

    /**
     * 单节点详情（暂时不用）
     */
    @GetMapping("/node/{id}")
    public Result nodeDetail(@PathVariable("id") String id) {
        return homePageService.getNodeDetail(id);
    }

    /**
     * L4 详情：按 ES 节点 id 查 MySQL hermes_agent_l4_detail 整行。
     * <p>
     * 请求：{@code GET /api/agent/l4/detail?esId=cap_agent_twitter_posts}
     */
    @GetMapping("/l4/detail")
    public Result l4Detail(@RequestParam("esId") String esId) {
        return homePageService.getL4DetailByEsId(esId);
    }

    /**
     * 首页看板：汇总数字 + 近7天图表，一次性返回。
     * <p>
     * 请求：{@code GET /api/agent/homepage/dashboard}
     * <p>
     * 成功时看 {@code records}，字段含义：
     * <pre>
     * todayUsage                 今日用量（数字）
     * historyTaskTotal           历史任务总数（数字）
     * historyTaskCompletionRate  历史任务完成率（整数，98 → 展示 98%）
     * agentTotal                 Agent 总数（数字）
     * agentOnline                Agent 在线数量（数字）
     * agentOnlineRate            Agent 在线率（整数，97 → 展示 97%）
     * charts.dates               近7天日期 X 轴，yyyy-MM-dd，升序
     * charts.verify              账号核查类柱状图；同时作调研趋势「核查类」曲线（同一数组）
     * charts.report              调研趋势「写报类」曲线
     * charts.social              社交类调用统计曲线
     * charts.business            业务专属类调用统计曲线
     * </pre>
     * charts 内各数量数组与 dates 下标一一对应。
     */
    @GetMapping("/homepage/dashboard")
    public Result dashboard() {
        return homePageService.getDashboard();
    }
}
