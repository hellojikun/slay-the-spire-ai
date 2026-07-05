# MCPTheSpire 启动说明

记录时间：2026-07-04

## 已确认环境

- 游戏目录：`E:\steamApp\steamapps\common\SlayTheSpire`
- ModTheSpire Workshop 目录：`E:\steamApp\steamapps\workshop\content\646570\1605060445`
- BaseMod Workshop 目录：`E:\steamApp\steamapps\workshop\content\646570\1605833019`
- MCPTheSpire Workshop 目录：`E:\steamApp\steamapps\workshop\content\646570\3632714834`
- MCP 地址：`http://127.0.0.1:8080/mcp`

## 关键配置

ModTheSpire 的默认 mod 列表文件：

```text
C:\Users\jikun\AppData\Local\ModTheSpire\mod_lists.json
```

当前需要包含：

```json
{
  "defaultList": "<Default>",
  "lists": {
    "<Default>": [
      "BaseMod.jar",
      "MCPTheSpire.jar"
    ]
  }
}
```

说明：这个文件里保存的是 jar 文件名，不是 mod id。

## 启动命令

在 PowerShell 中运行：

```powershell
Start-Process `
  -FilePath "E:\steamApp\steamapps\common\SlayTheSpire\jre\bin\java.exe" `
  -ArgumentList @(
    "-jar",
    "E:\steamApp\steamapps\workshop\content\646570\1605060445\ModTheSpire.jar",
    "--skip-launcher",
    "--mods",
    "basemod,MCPTheSpire"
  ) `
  -WorkingDirectory "E:\steamApp\steamapps\common\SlayTheSpire"
```

成功时窗口标题应显示 `Modded Slay the Spire`，并且 8080 端口会监听。

## 检查 MCP 是否启动

```powershell
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 8080 }
```

或：

```powershell
curl.exe http://127.0.0.1:8080/mcp
```

正常返回类似：

```json
{"name":"MCPTheSpire","transport":"streamable-http","version":"1.0.0"}
```

## 日志位置

这些日志适合排查“有没有启动、mod 有没有加载、是否报错”，但不适合当作策略复盘。

- ModTheSpire 启动器日志：`E:\steamApp\steamapps\common\SlayTheSpire\sendToDevs\mts_launcher.log`
- ModTheSpire / mod 加载日志：`E:\steamApp\steamapps\common\SlayTheSpire\sendToDevs\mts_process_launch.log`
- 游戏本体日志目录：`E:\steamApp\steamapps\common\SlayTheSpire\sendToDevs\logs`
- 游戏结算后的 run 文件：`E:\steamApp\steamapps\common\SlayTheSpire\runs`

当前进程命令行应类似：

```text
"E:\steamApp\steamapps\common\SlayTheSpire\jre\bin\java.exe" -jar E:\steamApp\steamapps\workshop\content\646570\1605060445\ModTheSpire.jar --skip-launcher --mods basemod,MCPTheSpire
```

## 注意事项

- 如果只用 Steam 原生启动，可能进入普通 `Slay the Spire`，MCP 不会监听。
- 如果 ModTheSpire 默认列表为空，可能看起来启动成功，但不会加载 MCPTheSpire。
- PowerShell 下直接用 `curl --data "{...}"` 容易把 JSON 转义弄坏；建议用 Python 标准库发送 JSON-RPC。
- `start_game` 在已有可继续存档时可能被拒绝，此时 MCP 会提示可用命令是 `continue`、`abandon`、`state`。如果要从新局开始，需要先确认是否放弃当前存档。
