/**
 * YouTube 深度人物画像：单轮主采集 + 成稿契约（对齐 Twitter get_user_tweets_for_persona）
 */
import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { YouTubeService } from './youtube-service.js';

interface VideoRow {
  videoId: string;
  title: string;
  description: string;
  publishedAt: string;
  viewCount: number;
  likeCount: number;
  commentCount: number;
  transcriptExcerpt: string;
}

const PRE_ANALYSIS_H2 = [
  '## 一、账号标识与资料画像',
  '## 二、账号规模与影响力',
  '## 三、地域属性与时空规律',
  '## 四、跨平台关联与联络线索',
  '## 五、内容议题与表达风格',
  '## 六、运营节奏与行为模式',
  '## 七、身份线索与关系网络',
];

const FORMAL_H2 = [
  '## 一、人物基本信息',
  '## 二、发文观点总结与立证',
  '## 三、其他平台账号与发文分析',
  '## 四、真实人物画像推断',
  '## 五、核查思路',
  '## 六、人物深度报告画像',
];

function citationLine(v: VideoRow): string {
  const date = (v.publishedAt || '').slice(0, 10) || '未知日期';
  const body = v.transcriptExcerpt
    || `${v.title}${v.description ? ` — ${v.description.slice(0, 180)}` : ''}`;
  const safe = body.replace(/」/g, "'").replace(/\n/g, ' ').trim();
  return `${date}｜播放${v.viewCount}｜赞${v.likeCount}｜「${safe}」`;
}

function buildContract(hasOcr: boolean) {
  const order = [
    '# 结构化预分析',
    ...PRE_ANALYSIS_H2,
    '# 正式人物画像报告',
    ...FORMAL_H2,
  ];
  if (hasOcr) {
    order.push('## 图文转换内容（已融合进分析中）');
  }
  return {
    structure_mode: 'TWO_H1_PARTS_REQUIRED',
    single_response_order: order,
    draft_first_line_must_be: '# 结构化预分析',
    draft_second_h1_must_be: '# 正式人物画像报告',
    pre_analysis_h2_locked: PRE_ANALYSIS_H2,
    formal_report_h2_locked: FORMAL_H2,
    forbidden_titles: [
      'OSINT 全景人物画像报告',
      '深度人物画像报告',
      '深度人物画像报告：',
      '一、人物基本信息（表格）',
      '二、可信网络身份确认',
      '综合评估',
      '📌 综合评估',
    ],
    forbidden_formats: [
      '跳过 # 结构化预分析',
      '用 Markdown 表格代替第二节 ≥10 条立证行',
      '正文 emoji（含 ✅❌⚠️📌）',
      '取证未完成就输出报告正文',
      '自创六节（可信网络身份/职业经历/综合评估）',
    ],
    section2_rule:
      '第二节须逐条粘贴 section2_citation_template.citation_lines；格式「日期｜播放｜赞｜「原文」」',
    section5_rule:
      '五、核查思路：标题下直接写「关于××」分块依据；禁止开篇套话（面向甲方/核查方、证据来源与推理路径、关于第五节本身）；禁工具名/表格',
  };
}

function buildComplianceGate(hasOcr: boolean) {
  const pending = ['mcp_maigret_collect_accounts（top_sites=10, timeout=150）'];
  if (hasOcr) {
    pending.unshift(
      'vision_analyze(image_url=media_for_ocr 每项, question=人物/文字/场景；URL 失败再用 base64 重试)',
    );
    pending.unshift('mcp_ocr_perform_batch_ocr（频道头像 URL，chi_sim）');
  }
  const gate: Record<string, unknown> = {
    status: 'DATA_COLLECTED_NOT_READY_TO_DRAFT',
    message:
      'YouTube 主平台数据已返回；禁止立即写报告或自创「OSINT 全景人物画像」。' +
      '须先完成 pending_tools_before_draft，再按 mandatory_output_contract 单次成稿。',
    pending_tools_before_draft: pending,
    draft_first_line_must_be: '# 结构化预分析',
    draft_second_h1_must_be: '# 正式人物画像报告',
    draft_forbidden_first_lines: [
      'OSINT 全景人物画像报告',
      '深度人物画像报告',
      '一、人物基本信息',
      '二、可信网络身份确认',
    ],
  };
  if (hasOcr) {
    gate.mandatory_builtin_tools_before_maigret = ['vision_analyze'];
    gate.forbid_maigret_until_vision_analyze = true;
    gate.vision_analyze_rule =
      '内置工具 vision_analyze（非 mcp_ 前缀）；OCR 后、maigret 前必调；日志须出现 preparing vision_analyze';
    gate.vision_independent_of_ocr =
      'OCR 无文字/识别差/返回空 均不得跳过 vision_analyze；只要 media_for_ocr 非空就必须调多模态';
    gate.forbidden_skip_vision_reasons = [
      'OCR未识别出有效文字',
      'OCR无文字所以跳过vision',
      '头像无可读文字',
    ];
    gate.forbidden_tools_during_pending = [
      'browser_console',
      'browser_console_messages',
      'browser_vision',
      'browser_navigate',
      'browser_snapshot',
      'mcp_playwright_browser_navigate',
      'mcp_playwright_browser_take_screenshot',
      'mcp_playwright_browser_console_messages',
    ];
    gate.vision_not_browser_rule =
      '禁止用 browser_* / console 代替 vision_analyze；须直接调工具 vision_analyze(image_url, question)';
  }
  return gate;
}

function personaBanner(gate: ReturnType<typeof buildComplianceGate>): string {
  const pending = (gate.pending_tools_before_draft as string[]).join(' → ');
  const visionBlock =
    gate.forbid_maigret_until_vision_analyze
      ? '【必调多模态】有图（media_for_ocr 非空）必调 vision_analyze；OCR 无文字也禁止跳过\n' +
        'OCR 完成后必须调内置工具 vision_analyze（非 mcp_）；未完成禁止 maigret\n' +
        '调用示例：vision_analyze(image_url=media_for_ocr[0], question="人物/文字/场景")\n' +
        '禁止：browser_console / browser_* / mcp_playwright_* 代替 vision_analyze\n' +
        '日志须出现：preparing vision_analyze\n'
      : '';
  return (
    '【画像合规门禁 / 禁止立即成稿】\n' +
    visionBlock +
    '成稿第一行：# 结构化预分析（必须有#号）\n' +
    '然后：# 正式人物画像报告（六节固定标题）\n' +
    '第二节：从 section2_citation_template 逐条粘贴「日期｜播放｜赞｜「原文」」\n' +
    '有图时：OCR → vision_analyze → maigret（OCR 与 vision 可同轮）；图文节含 OCR + 多模态\n' +
    '禁止：OSINT 全景人物画像报告 / 表格堆砌 / emoji / 自创章节\n' +
    `取证后续：${pending}\n` +
    '---JSON_BELOW---\n'
  );
}

async function resolveChannel(
  yt: YouTubeService,
  params: { query?: string; channel_id?: string; channel_url?: string },
): Promise<{ channelId: string; title: string; resolve: Record<string, unknown> }> {
  // 统一走服务层：UC / @handle / URL / 搜索
  const raw = (params.channel_id || params.channel_url || params.query || '').trim();
  if (!raw) {
    throw new Error('请提供 query（频道名）、channel_id 或 channel_url');
  }
  const resolved = await yt.resolveChannelId(raw);
  const ch = await yt.getChannelDetails(resolved.channelId);
  const item = ch.items?.[0];
  return {
    channelId: resolved.channelId,
    title: item?.snippet?.title || resolved.channelId,
    resolve: {
      source: resolved.source,
      resolvedFrom: resolved.resolvedFrom,
      input: raw,
    },
  };
}

async function fetchChannelVideos(
  yt: YouTubeService,
  channelId: string,
  maxResults: number,
): Promise<VideoRow[]> {
  const searchResponse = await yt.youtube.search.list({
    part: ['snippet'],
    channelId,
    maxResults,
    order: 'date',
    type: ['video'],
  });
  const videoIds =
    searchResponse.data.items
      ?.map((item) => item.id?.videoId)
      .filter((id): id is string => Boolean(id)) || [];
  if (!videoIds.length) return [];

  const videosResponse = await yt.youtube.videos.list({
    part: ['snippet', 'statistics', 'contentDetails'],
    id: videoIds,
  });

  return (videosResponse.data.items || []).map((video) => ({
    videoId: video.id || '',
    title: video.snippet?.title || '',
    description: (video.snippet?.description || '').slice(0, 500),
    publishedAt: video.snippet?.publishedAt || '',
    viewCount: Number(video.statistics?.viewCount || 0),
    likeCount: Number(video.statistics?.likeCount || 0),
    commentCount: Number(video.statistics?.commentCount || 0),
    transcriptExcerpt: '',
  }));
}

async function tryTranscripts(yt: YouTubeService, videos: VideoRow[], limit: number): Promise<void> {
  const targets = [...videos].sort((a, b) => b.viewCount - a.viewCount).slice(0, limit);
  for (const v of targets) {
    for (const lang of ['zh', 'zh-Hans', 'zh-CN', undefined] as const) {
      try {
        const segs = await yt.getTranscript(v.videoId, lang);
        if (segs?.length) {
          v.transcriptExcerpt = segs
            .slice(0, 8)
            .map((s) => s.text)
            .join(' ')
            .slice(0, 320);
          break;
        }
      } catch {
        /* 单条字幕失败不抛出不中断进程 */
      }
    }
  }
}

export function registerPersonaTools(server: McpServer, youtubeService: YouTubeService): void {
  server.tool(
    'get-channel-for-persona',
    '【YouTube 画像第一轮唯一主工具】采集频道资料+近期视频立证包，返回 compliance_gate 与 mandatory_output_contract。' +
      '禁止立即成稿；须 OCR(若有头像)→maigret 后再输出 # 结构化预分析 + # 正式人物画像报告。',
    {
      query: z.string().optional().describe('频道名，如 江峰时刻'),
      channel_id: z.string().optional().describe('UC 开头的 channelId'),
      channel_url: z.string().optional(),
      max_videos: z.number().min(5).max(50).optional(),
    },
    async ({ query, channel_id, channel_url, max_videos = 20 }) => {
      try {
        const resolved = await resolveChannel(youtubeService, { query, channel_id, channel_url });
        const channelId = resolved.channelId;
        const chData = await youtubeService.getChannelDetails(channelId);
        const ch = chData.items?.[0];
        const sn = ch?.snippet;
        const st = ch?.statistics;

        const videos = await fetchChannelVideos(youtubeService, channelId, max_videos);
        await tryTranscripts(youtubeService, videos, 3);

        const thumb =
          sn?.thumbnails?.high?.url ||
          sn?.thumbnails?.medium?.url ||
          sn?.thumbnails?.default?.url ||
          '';
        const mediaForOcr = thumb ? [thumb] : [];

        const evidencePack = videos.map((v) => ({
          video_id: v.videoId,
          date: (v.publishedAt || '').slice(0, 10),
          views: v.viewCount,
          likes: v.likeCount,
          comments: v.commentCount,
          title: v.title,
          description_preview: v.description.slice(0, 200),
          transcript_excerpt: v.transcriptExcerpt,
          citation_line: citationLine(v),
        }));

        const citationLines = evidencePack.map((e) => e.citation_line);
        const hasOcr = mediaForOcr.length > 0;
        const gate = buildComplianceGate(hasOcr);
        const contract = buildContract(hasOcr);
        const section2 = {
          required_h2_exactly: '## 二、发文观点总结与立证',
          min_citation_lines: 10,
          min_section_chars: 1200,
          format_rule: '逐条粘贴 citation_lines；无字幕时用标题+描述，须标注「无字幕」',
          citation_lines_copy_into_section2: citationLines.slice(0, 14),
        };

        const payload = {
          compliance_gate: gate,
          mandatory_output_contract: contract,
          section2_citation_template: section2,
          analysis_target_lock: {
            platform: 'youtube',
            channel_id: channelId,
            channel_title: resolved.title,
            query: query || resolved.title,
            session_isolation_rules: [
              `本返回体仅服务于 YouTube 频道 ${resolved.title}（${channelId}）`,
              '禁止套用其它平台/上一轮人物结论',
            ],
          },
          channel_profile: {
            channel_id: channelId,
            title: sn?.title || resolved.title,
            description: sn?.description || '',
            published_at: sn?.publishedAt || '',
            country: sn?.country || '',
            subscriber_count: st?.subscriberCount,
            video_count: st?.videoCount,
            view_count: st?.viewCount,
            thumbnail_url: thumb,
            banner_url: '',
          },
          video_evidence_pack: evidencePack,
          media_for_ocr: mediaForOcr,
          sample_coverage: {
            videos_fetched: videos.length,
            transcript_attempted: Math.min(3, videos.length),
            transcript_ok: videos.filter((v) => v.transcriptExcerpt).length,
            note:
              videos.length < 10
                ? `频道公开视频样本 ${videos.length} 条（API 返回量）；第二节仍须尽量用足 citation_lines`
                : `近 ${videos.length} 条视频元数据；字幕成功 ${videos.filter((v) => v.transcriptExcerpt).length} 条`,
          },
          resolve: resolved.resolve,
        };

        return {
          content: [{ type: 'text' as const, text: personaBanner(gate) + JSON.stringify(payload, null, 2) }],
        };
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        return {
          content: [{ type: 'text' as const, text: JSON.stringify({ error: msg, hint: '检查 query/channel_id 或 API Key' }) }],
          isError: true,
        };
      }
    },
  );
}
