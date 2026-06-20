using System.Diagnostics;
using System.Text.Json;

namespace DiscordTranscriber.Native.Services;

public sealed class BackendService : IDisposable
{
    private readonly RepositoryPaths _paths;
    private Process? _process;
    private readonly object _sync = new();

    public event Action<string>? LogLine;
    public event Action<string>? StateChanged;
    public event Action<string>? ProgressChanged;

    public BackendService(RepositoryPaths paths)
    {
        _paths = paths;
    }

    public bool IsRunning
    {
        get
        {
            lock (_sync)
            {
                return _process is { HasExited: false };
            }
        }
    }

    public Task<bool> StartAsync()
    {
        lock (_sync)
        {
            if (_process is { HasExited: false })
            {
                return Task.FromResult(true);
            }

            Directory.CreateDirectory(_paths.NativeUi);
            var useVenv = File.Exists(_paths.VenvPythonPath);
            var start = new ProcessStartInfo
            {
                FileName = useVenv ? _paths.VenvPythonPath : "py",
                Arguments = useVenv
                    ? "-u \"..\\src\\app.py\" --backend"
                    : "-3 -u \"..\\src\\app.py\" --backend",
                WorkingDirectory = _paths.NativeUi,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };

            start.Environment["PYTHONUTF8"] = "1";
            start.Environment["PYTHONUNBUFFERED"] = "1";
            if (!start.Environment.ContainsKey("VT_INJECT_OVERLAY"))
            {
                start.Environment["VT_INJECT_OVERLAY"] = "1";
            }

            try
            {
                _process = new Process { StartInfo = start, EnableRaisingEvents = true };
                _process.OutputDataReceived += (_, e) => OnOutput(e.Data);
                _process.ErrorDataReceived += (_, e) => OnOutput(e.Data);
                _process.Exited += (_, _) => StateChanged?.Invoke("stopped");
                _process.Start();
                _process.BeginOutputReadLine();
                _process.BeginErrorReadLine();
                StateChanged?.Invoke("running");
                return Task.FromResult(true);
            }
            catch (Exception ex)
            {
                LogLine?.Invoke("[native] failed to start backend: " + ex.Message);
                _process = null;
                StateChanged?.Invoke("stopped");
                return Task.FromResult(false);
            }
        }
    }

    public async Task StopAsync()
    {
        Process? proc;
        lock (_sync)
        {
            proc = _process;
            _process = null;
        }

        await CleanupOverlaysAsync().ConfigureAwait(false);

        if (proc is null)
        {
            StateChanged?.Invoke("stopped");
            return;
        }

        try
        {
            if (!proc.HasExited)
            {
                proc.Kill(entireProcessTree: true);
                await proc.WaitForExitAsync().ConfigureAwait(false);
            }
        }
        catch
        {
            // Best effort shutdown. The UI still moves back to stopped.
        }
        finally
        {
            proc.Dispose();
            StateChanged?.Invoke("stopped");
        }
    }

    private async Task CleanupOverlaysAsync()
    {
        Directory.CreateDirectory(_paths.NativeUi);
        var useVenv = File.Exists(_paths.VenvPythonPath);
        var start = new ProcessStartInfo
        {
            FileName = useVenv ? _paths.VenvPythonPath : "py",
            Arguments = useVenv
                ? "-u \"..\\src\\app.py\" --cleanup-overlay"
                : "-3 -u \"..\\src\\app.py\" --cleanup-overlay",
            WorkingDirectory = _paths.NativeUi,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };

        start.Environment["PYTHONUTF8"] = "1";
        try
        {
            using var cleanup = Process.Start(start);
            if (cleanup is null)
            {
                return;
            }

            var outputTask = cleanup.StandardOutput.ReadToEndAsync();
            var errorTask = cleanup.StandardError.ReadToEndAsync();
            var exitTask = cleanup.WaitForExitAsync();
            var complete = await Task.WhenAny(exitTask, Task.Delay(TimeSpan.FromSeconds(8))).ConfigureAwait(false);
            if (complete != exitTask)
            {
                try { cleanup.Kill(entireProcessTree: true); } catch { }
                LogLine?.Invoke("[native] overlay cleanup timed out");
                return;
            }

            var output = await outputTask.ConfigureAwait(false);
            var error = await errorTask.ConfigureAwait(false);
            foreach (var line in (output + error).Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
            {
                LogLine?.Invoke(line);
            }
        }
        catch (Exception ex)
        {
            LogLine?.Invoke("[native] overlay cleanup failed: " + ex.Message);
        }
    }

    private void OnOutput(string? line)
    {
        if (string.IsNullOrWhiteSpace(line))
        {
            return;
        }

        if (line.StartsWith("[[VTPROG]]", StringComparison.Ordinal))
        {
            try
            {
                using var doc = JsonDocument.Parse(line["[[VTPROG]]".Length..]);
                if (doc.RootElement.TryGetProperty("label", out var label))
                {
                    ProgressChanged?.Invoke(label.GetString() ?? "");
                }
            }
            catch
            {
                ProgressChanged?.Invoke("");
            }

            return;
        }

        LogLine?.Invoke(line);
    }

    public void Dispose()
    {
        StopAsync().GetAwaiter().GetResult();
    }
}
