using System.Net.Http;
using System.Reflection;
using System.Text.Json.Nodes;

namespace DiscordTranscriber.Native.Services;

public sealed record UpdateInfo(string Status, bool UpdateAvailable, string LatestVersion);

// Checks the current assembly version against the latest GitHub release of ellypaws/whispercord.
public sealed class UpdateService
{
    private const string Owner = "ellypaws";
    private const string Repo = "whispercord";

    public string CurrentVersion { get; }
    public string RepoUrl => $"https://github.com/{Owner}/{Repo}";
    public string IssuesUrl => $"{RepoUrl}/issues";
    public string NewIssueUrl => $"{RepoUrl}/issues/new/choose";
    public string ReleasesUrl => $"{RepoUrl}/releases";

    public UpdateService()
    {
        var v = Assembly.GetExecutingAssembly().GetName().Version;
        CurrentVersion = v is null ? "0.0.0" : $"{v.Major}.{v.Minor}.{v.Build}";
    }

    public async Task<UpdateInfo> CheckAsync()
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("whispercord-native");
            http.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");

            using var response = await http.GetAsync(
                $"https://api.github.com/repos/{Owner}/{Repo}/releases/latest").ConfigureAwait(false);

            if (response.StatusCode == System.Net.HttpStatusCode.NotFound)
            {
                return new UpdateInfo($"You are on v{CurrentVersion} (no published releases yet).", false, "");
            }

            response.EnsureSuccessStatusCode();
            var json = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            var tag = JsonNode.Parse(json)?["tag_name"]?.GetValue<string>() ?? "";
            var latest = NormalizeTag(tag);

            if (Version.TryParse(latest, out var latestVer) && Version.TryParse(CurrentVersion, out var currentVer))
            {
                return latestVer > currentVer
                    ? new UpdateInfo($"Update available: v{latest} (you have v{CurrentVersion}).", true, latest)
                    : new UpdateInfo($"You are up to date (v{CurrentVersion}).", false, latest);
            }

            return new UpdateInfo($"Latest release: {tag} (you have v{CurrentVersion}).", false, latest);
        }
        catch
        {
            return new UpdateInfo($"Could not check for updates (v{CurrentVersion}).", false, "");
        }
    }

    private static string NormalizeTag(string tag)
        => tag.Replace("native-", "", StringComparison.OrdinalIgnoreCase).TrimStart('v', 'V').Trim();
}
