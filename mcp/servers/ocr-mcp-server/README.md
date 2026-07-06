# OCR MCP（mcp-ocr + Tesseract）

## 工具

| Hermes 工具名 | 用途 |
|---------------|------|
| `mcp_ocr_perform_ocr` | 单张图 OCR（本地路径 / URL / base64） |
| `mcp_ocr_perform_batch_ocr` | 多张图并发 OCR |
| `mcp_ocr_get_supported_languages` | 查看已安装语言 |

## 中文场景

调用时建议：`language=chi_sim`（中文）或 `language=eng`（英文）；**勿用** `chi_sim+eng`（会报错）。

## 依赖

- Python：`pip install mcp-ocr`
- Tesseract：已通过 winget 安装到 `C:\Program Files\Tesseract-OCR`
- 中文语言包：见 `tessdata/chi_sim.traineddata`（`TESSDATA_PREFIX` 指向本目录）

## 运维

改 config 后：`/reload-mcp`

若 Tesseract 路径不同，修改 `config.yaml` → `mcp_servers.ocr.env.TESSERACT_CMD`
