# DiscordTranscriber.Native

Native WinUI 3 sidecar for Discord Live Transcriber.

## Build

Install the .NET SDK and Windows App SDK tooling, then run from the repo root:

```powershell
dotnet restore native-ui\DiscordTranscriber.Native.csproj
dotnet build native-ui\DiscordTranscriber.Native.csproj -c Debug
```

## Runtime shape

- Keeps the existing Python backend unchanged.
- Starts the backend from `native-ui` with `..\.venv\Scripts\python.exe -u ..\src\app.py --backend`.
- Falls back to `py -3 -u ..\src\app.py --backend` if the venv interpreter is missing.
- Reads and writes the existing root `config.json`.
- Consumes the existing relay at `ws://127.0.0.1:{relay_port}`.
- Uses Mica for the main window and keeps Acrylic scoped to transient surface resources.
