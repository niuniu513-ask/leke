# 客户端源码与构建

客户端对应源码位于 `client/`。1.6.31 Release 的 `leke-1.6.31-client-source.zip` 是包含语音模型的完整源码包；服务端独立维护，不在此仓库分发。

## Windows 开发环境

使用 Windows x64、Python 3.13，在仓库根目录执行：

```powershell
cd client
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install PySide6 selenium PyInstaller
.\.venv\Scripts\python.exe 乐课.py
```

GUI 和浏览器依赖需额外安装，如上所示。平台浏览器功能还需要可用的 Microsoft Edge 及其匹配驱动或驱动自动下载网络权限。

GitHub 单文件大小限制使 Git 目录未收录 `resource/whisper-base.en/model.bin`。构建完整版本前，从同版完整源码 ZIP 将该文件复制到 `client/resource/whisper-base.en/model.bin`。其他源码和资源与完整包一致。

## 构建

在 `client/` 中执行：

```powershell
.\.venv\Scripts\python.exe build_exe.py --onefile
```

产物位于 `dist/`。首次构建可能需要下载依赖；构建输出受本机 Python、依赖及打包工具版本影响，不保证二进制逐字节一致。正式程序需要正常登录乐课服务，源码不包含管理员账号或私有服务端密钥。

## 许可证与数据

保留 [第三方声明](client/THIRD_PARTY_NOTICES.md)、[GPL-3.0](client/licenses/chaoxing-GPL-3.0.txt)、[U校园 AGPL-3.0](client/api/unipus/LICENSE) 及各组件 NOTICE。各组件按适用许可证分发，不能将整个客户端描述为闭源专有软件。

源码内公开协议常量和客户端接入标识不构成私有凭据或授权边界。运行时生成的配置、账号、Cookie、日志及浏览器档案不属于公开源码，切勿提交。
