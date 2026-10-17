using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;

// Usage: loadtest <assembly.dll> <dependency dir>
var path = Path.GetFullPath(args[0]);
var depDir = args[1];
try { Console.WriteLine($"ProcessorArchitecture: {AssemblyName.GetAssemblyName(path).ProcessorArchitecture}"); }
catch (Exception e) { Console.WriteLine($"GetAssemblyName FAILED: {e.GetType().Name}: {e.Message}"); }
var alc = new AssemblyLoadContext("game", isCollectible: false);
alc.Resolving += (ctx, name) => { var p = Path.Combine(depDir, name.Name + ".dll"); return File.Exists(p) ? ctx.LoadFromAssemblyPath(p) : null; };
try
{
    var asm = alc.LoadFromAssemblyPath(path);
    var t = asm.GetType("GodotPlugins.Game.Main", throwOnError: true);
    var m = t.GetMethod("InitializeFromGameProject", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
    Console.WriteLine($"LOAD OK: {asm.GetName().Name}, entry: {t.FullName}.{m?.Name}");
    int ok = 0, bad = 0;
    foreach (var ty in asm.GetTypes()) { try { _ = ty.GetMethods(); ok++; } catch { bad++; } }
    Console.WriteLine($"types resolved: {ok}, failed: {bad}");
}
catch (Exception e) { Console.WriteLine($"LOAD FAILED: {e.GetType().Name}: {e.Message}"); }
