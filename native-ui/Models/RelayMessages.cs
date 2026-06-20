using System.Text.Json.Serialization;

namespace DiscordTranscriber.Native.Models;

public sealed class RelayClientStatus
{
    [JsonPropertyName("hooked")]
    public bool Hooked { get; set; }

    [JsonPropertyName("cdp")]
    public bool Cdp { get; set; }

    [JsonPropertyName("streams")]
    public int Streams { get; set; }

    [JsonPropertyName("active")]
    public int Active { get; set; }

    [JsonPropertyName("mapped")]
    public int Mapped { get; set; }
}
