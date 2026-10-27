// Compiled locally by install-native-verification.ps1. No downloaded executable.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

internal static class NativeLauncher
{
    // Windows CommandLineToArgvW quoting, including embedded quotes/trailing slashes.
    private static string Quote(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"')
            {
                result.Append('\\', slashes * 2 + 1);
                result.Append('"');
                slashes = 0;
                continue;
            }
            result.Append('\\', slashes);
            slashes = 0;
            result.Append(ch);
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    private static int Main(string[] args)
    {
        try
        {
            string folder = Path.GetDirectoryName(typeof(NativeLauncher).Assembly.Location);
            string root = Path.GetFullPath(Path.Combine(folder, "..", ".."));
            string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            string host = Path.Combine(root, "native_verification", "host.py");
            string manifest = Path.Combine(folder, "host.json");
            if (!File.Exists(python) || !File.Exists(host) || !File.Exists(manifest)) return 1;
            StringBuilder arguments = new StringBuilder("-u " + Quote(host));
            foreach (string arg in args) arguments.Append(" " + Quote(arg));
            arguments.Append(" --host-manifest " + Quote(manifest));
            ProcessStartInfo start = new ProcessStartInfo(python, arguments.ToString());
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WorkingDirectory = root;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            // Copy binary streams explicitly: .NET's default inheritance can
            // retain invalid/non-inheritable Chrome handles. Never use ReadLine.
            using (Process child = Process.Start(start))
            {
                Thread input = new Thread(delegate() {
                    try { Console.OpenStandardInput().CopyTo(child.StandardInput.BaseStream); }
                    catch { }
                    finally { try { child.StandardInput.Close(); } catch { } }
                });
                input.IsBackground = true;
                input.Start();
                Thread errors = new Thread(delegate() {
                    try { child.StandardError.BaseStream.CopyTo(Stream.Null); }
                    catch { }
                });
                errors.IsBackground = true;
                errors.Start();
                child.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput());
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch { return 1; } // Do not print paths, arguments or raw errors.
    }
}
