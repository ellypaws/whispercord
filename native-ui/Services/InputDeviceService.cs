using System.Diagnostics;
using System.Text.Json;
using DiscordTranscriber.Native.Models;

namespace DiscordTranscriber.Native.Services;

public sealed class InputDeviceService
{
    private readonly RepositoryPaths _paths;

    public InputDeviceService(RepositoryPaths paths)
    {
        _paths = paths;
    }

    public async Task<List<InputDeviceInfo>> ListAsync()
    {
        var result = new List<InputDeviceInfo>
        {
            new() { Index = null, Name = "System default" }
        };

        if (!File.Exists(_paths.VenvPythonPath))
        {
            return result;
        }

        const string script = "import json, sounddevice as sd; seen=set(); out=[]; [out.append({'index':i,'name':d.get('name','')}) or seen.add(d.get('name','')) for i,d in enumerate(sd.query_devices()) if d.get('max_input_channels',0)>0 and d.get('name','') not in seen]; print(json.dumps(out))";
        var start = new ProcessStartInfo
        {
            FileName = _paths.VenvPythonPath,
            Arguments = "-c " + Quote(script),
            WorkingDirectory = _paths.Root,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };

        try
        {
            using var proc = Process.Start(start);
            if (proc is null)
            {
                return result;
            }

            var output = await proc.StandardOutput.ReadToEndAsync().ConfigureAwait(false);
            await proc.WaitForExitAsync().ConfigureAwait(false);
            var devices = JsonSerializer.Deserialize<List<DeviceDto>>(output) ?? new List<DeviceDto>();
            result.AddRange(devices.Select(d => new InputDeviceInfo { Index = d.Index, Name = d.Name ?? $"Device {d.Index}" }));
        }
        catch
        {
            return result;
        }

        return result;
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    private sealed class DeviceDto
    {
        public int Index { get; set; }
        public string? Name { get; set; }
    }
}
