package com.example.aw.collect.controller;

import com.example.aw.collect.service.LoginService;
import com.example.aw.entity.Result;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.annotation.Resource;
import java.util.Map;

/**
 * 登录
 */
@RestController
@RequestMapping("/api")
public class LoginController {

    @Resource
    private LoginService loginService;

    /**
     * 账密登录，返回 user_id
     * 前端传：{"username":"xxx","password":"yyy"}
     */
    @PostMapping("/login")
    public Result login(@RequestBody Map<String, Object> body) {
        String userName = null;
        String passWord = null;
        if (body != null) {
            Object u = body.get("username");
            if (u == null) {
                u = body.get("userName");
            }
            Object p = body.get("password");
            if (p == null) {
                p = body.get("passWord");
            }
            userName = u == null ? null : String.valueOf(u);
            passWord = p == null ? null : String.valueOf(p);
        }
        return loginService.login(userName, passWord);
    }
}
