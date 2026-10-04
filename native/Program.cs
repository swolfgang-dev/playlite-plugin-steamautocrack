using Steamless.API.Model;
using Steamless.API.Services;

if (args.Length != 1) { Console.Error.WriteLine("Usage: NativeSteamless FILE"); return 2; }
var log = new LoggingService();
log.AddLogMessage += (_, message) => Console.WriteLine(message.Message);
var options = new SteamlessOptions { KeepBindSection = true, ZeroDosStubData = true,
    DontRealignSections = true, RecalculateFileChecksum = false };
SteamlessPlugin[] plugins = [new Steamless.Unpacker.Variant10.x86.Main(),
    new Steamless.Unpacker.Variant20.x86.Main(), new Steamless.Unpacker.Variant21.x86.Main(),
    new Steamless.Unpacker.Variant30.x86.Main(), new Steamless.Unpacker.Variant30.x64.Main(),
    new Steamless.Unpacker.Variant31.x86.Main(), new Steamless.Unpacker.Variant31.x64.Main()];
foreach (var plugin in plugins) {
    if (plugin.Initialize(log) && plugin.CanProcessFile(args[0]))
        return plugin.ProcessFile(args[0], options) ? 0 : 1;
}
Console.Error.WriteLine("No supported SteamStub variant found.");
return 3;
