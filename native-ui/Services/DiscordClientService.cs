using System.Diagnostics;
using DiscordTranscriber.Native.Models;

namespace DiscordTranscriber.Native.Services;

public sealed class DiscordClientService
{
    private static readonly (string Folder, string Exe, int Port)[] Clients =
    {
        ("DiscordPTB", "DiscordPTB.exe", 9223),
        ("Discord", "Discord.exe", 9224),
        ("DiscordCanary", "DiscordCanary.exe", 9225),
        ("DiscordDevelopment", "DiscordDevelopment.exe", 9226)
    };

    public async Task<List<DiscordClientInfo>> ListClientsAsync(ConfigService config)
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var result = new List<DiscordClientInfo>();

        foreach (var (folder, exe, port) in Clients)
        {
            var root = Path.Combine(localAppData, folder);
            if (!Directory.Exists(root))
            {
                continue;
            }

            var path = Directory.EnumerateDirectories(root, "app-*")
                .Select(dir => Path.Combine(dir, exe))
                .Where(File.Exists)
                .OrderBy(x => x, StringComparer.OrdinalIgnoreCase)
                .LastOrDefault();

            if (path is null)
            {
                continue;
            }

            var info = new DiscordClientInfo
            {
                Folder = folder,
                Exe = exe.ToLowerInvariant(),
                ExePath = path,
                Port = port,
                IsRunning = IsRunning(exe),
                IsLive = await CdpAliveAsync(port).ConfigureAwait(false),
                OverlayEnabled = config.GetOverlayFor(exe.ToLowerInvariant()),
                SelfEnabled = config.GetSelfFor(exe.ToLowerInvariant())
            };

            result.Add(info);
        }

        return result;
    }

    public async Task<string> EnsureClientAsync(DiscordClientInfo client, bool restartIfNeeded)
    {
        if (await CdpAliveAsync(client.Port).ConfigureAwait(false))
        {
            return "ready";
        }

        if (IsRunning(client.Exe) && !restartIfNeeded)
        {
            return "running-no-port";
        }

        if (IsRunning(client.Exe))
        {
            KillClient(client.Exe);
            await Task.Delay(1500).ConfigureAwait(false);
        }

        LaunchClient(client.ExePath, client.Port);

        var deadline = DateTimeOffset.Now.AddSeconds(20);
        while (DateTimeOffset.Now < deadline)
        {
            if (await CdpAliveAsync(client.Port).ConfigureAwait(false))
            {
                return restartIfNeeded ? "restarted" : "launched";
            }

            await Task.Delay(500).ConfigureAwait(false);
        }

        return restartIfNeeded ? "restarted" : "launched";
    }

    private static bool IsRunning(string exeName)
    {
        var name = Path.GetFileNameWithoutExtension(exeName);
        return Process.GetProcessesByName(name).Length > 0;
    }

    private static void KillClient(string exeName)
    {
        foreach (var proc in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(exeName)))
        {
            try
            {
                proc.Kill(entireProcessTree: true);
            }
            catch
            {
                // Best effort; Discord may already be exiting.
            }
        }
    }

    private static void LaunchClient(string exePath, int port)
    {
        var start = new ProcessStartInfo
        {
            FileName = exePath,
            Arguments = $"--remote-debugging-port={port}",
            WorkingDirectory = Path.GetDirectoryName(exePath) ?? "",
            UseShellExecute = true
        };
        Process.Start(start);
    }

    private static async Task<bool> CdpAliveAsync(int port)
    {
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(1));
        using var client = new HttpClient();
        try
        {
            using var response = await client.GetAsync($"http://127.0.0.1:{port}/json/version", cts.Token)
                .ConfigureAwait(false);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }
}
