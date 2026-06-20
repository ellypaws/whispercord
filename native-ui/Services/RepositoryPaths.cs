namespace DiscordTranscriber.Native.Services;

public sealed class RepositoryPaths
{
    public string Root { get; }
    public string NativeUi { get; }
    public string ConfigPath => Path.Combine(Root, "config.json");
    public string VenvPythonPath => Path.Combine(Root, ".venv", "Scripts", "python.exe");

    public RepositoryPaths()
    {
        Root = FindRoot();
        NativeUi = Path.Combine(Root, "native-ui");
    }

    private static string FindRoot()
    {
        foreach (var start in CandidateStarts())
        {
            var dir = new DirectoryInfo(start);
            while (dir is not null)
            {
                if (File.Exists(Path.Combine(dir.FullName, "config.json")) &&
                    File.Exists(Path.Combine(dir.FullName, "src", "app.py")))
                {
                    return dir.FullName;
                }

                dir = dir.Parent;
            }
        }

        return Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, ".."));
    }

    private static IEnumerable<string> CandidateStarts()
    {
        yield return Environment.CurrentDirectory;
        yield return AppContext.BaseDirectory;
    }
}
