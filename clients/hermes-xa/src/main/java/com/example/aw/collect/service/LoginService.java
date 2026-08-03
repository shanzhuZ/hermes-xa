package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.entity.Result;
import org.elasticsearch.action.search.SearchRequest;
import org.elasticsearch.action.search.SearchResponse;
import org.elasticsearch.client.RequestOptions;
import org.elasticsearch.client.RestHighLevelClient;
import org.elasticsearch.index.query.BoolQueryBuilder;
import org.elasticsearch.index.query.QueryBuilders;
import org.elasticsearch.search.SearchHit;
import org.elasticsearch.search.SearchHits;
import org.elasticsearch.search.builder.SearchSourceBuilder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import javax.annotation.Resource;
import java.util.HashMap;
import java.util.Map;

/**
 * 登录：按账密查 ES，返回 user_id
 */
@Service
public class LoginService {

    @Resource
    private RestHighLevelClient restHighLevelClient5602;

    private static Logger logger = LoggerFactory.getLogger(LoginService.class);

    private static final String INDEX = "hermes_xa_login_info";

    /**
     * 账密登录
     */
    public Result login(String userName, String passWord) {
        try {
            if (userName == null || userName.trim().isEmpty()) {
                return new Result(400, "用户名不能为空", 0, 0, null);
            }
            if (passWord == null || passWord.trim().isEmpty()) {
                return new Result(400, "密码不能为空", 0, 0, null);
            }

            userName = userName.trim();
            passWord = passWord.trim();

            // 1. 构建查询条件（必须同时匹配）
            BoolQueryBuilder queryBuilder = QueryBuilders.boolQuery()
                    .must(QueryBuilders.termQuery("username.keyword", userName))
                    .must(QueryBuilders.termQuery("password.keyword", passWord));

            // 2. 构建 SearchRequest
            SearchRequest searchRequest = new SearchRequest(INDEX);
            SearchSourceBuilder sourceBuilder = new SearchSourceBuilder();
            sourceBuilder.query(queryBuilder);
            sourceBuilder.size(1);
            searchRequest.source(sourceBuilder);

            logger.info("ES DSL => {}", sourceBuilder.toString());

            // 3. 执行查询
            SearchResponse response = restHighLevelClient5602.search(searchRequest, RequestOptions.DEFAULT);

            // 4. 判断结果，返回 user_id
            SearchHits hits = response.getHits();
            if (hits.getTotalHits().value > 0) {
                SearchHit hit = hits.getHits()[0];
                Map<String, Object> source = JSON.parseObject(hit.getSourceAsString(), Map.class);
                Object userId = source.get("user_id");
                if (userId == null || String.valueOf(userId).trim().isEmpty()) {
                    return new Result(400, "账号未配置user_id", 0, 0, null);
                }

                Map<String, Object> records = new HashMap<>();
                records.put("user_id", String.valueOf(userId));
                return new Result(200, "登录成功", 1, 1, records);
            } else {
                return new Result(400, "用户名或密码错误", 0, 0, null);
            }

        } catch (Exception e) {
            logger.error("login error", e);
            return new Result(400, "登录异常", 0, 0, null);
        }
    }
}
