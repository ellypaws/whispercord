namespace DiscordTranscriber.Native.Services;

public sealed class CudaService
{
    private readonly RepositoryPaths _paths;

    public CudaService(RepositoryPaths paths)
    {
        _paths = paths;
    }

    public bool RuntimePresent()
    {
        var dirs = new List<string> { Path.Combine(_paths.Root, "cuda") };
        var sitePackages = Path.Combine(_paths.Root, ".venv", "Lib", "site-packages", "nvidia");

        if (Directory.Exists(sitePackages))
        {
            dirs.AddRange(Directory.EnumerateDirectories(sitePackages)
                .Select(x => Path.Combine(x, "bin")));
        }

        var haveBlas = dirs.Any(d => Directory.Exists(d) && Directory.EnumerateFiles(d, "cublas64*.dll").Any());
        var haveDnn = dirs.Any(d => Directory.Exists(d) && Directory.EnumerateFiles(d, "cudnn*.dll").Any());
        return haveBlas && haveDnn;
    }
}
