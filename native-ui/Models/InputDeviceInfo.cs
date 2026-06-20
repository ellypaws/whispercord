namespace DiscordTranscriber.Native.Models;

public sealed class InputDeviceInfo
{
    public int? Index { get; init; }
    public string Name { get; init; } = "";
    public string Value => Index?.ToString() ?? "";
}
