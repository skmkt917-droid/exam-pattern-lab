using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string window = Path.Combine(root, "app", "window.py");
        if (!File.Exists(window))
        {
            MessageBox.Show(
                "app\\window.py 를 찾지 못했습니다.\n" + window,
                "exam pattern lab",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        string pythonw = FindOnPath("pythonw.exe");
        if (pythonw == null)
        {
            pythonw = FindOnPath("pyw.exe");
        }
        if (pythonw == null)
        {
            MessageBox.Show(
                "pythonw 를 찾지 못했습니다. Python이 설치되어 있고 PATH에 있어야 합니다.",
                "exam pattern lab",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = pythonw,
                Arguments = "\"" + window + "\"",
                WorkingDirectory = root,
                UseShellExecute = false
            });
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "창을 열지 못했습니다.\n" + ex.Message,
                "exam pattern lab",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    static string FindOnPath(string fileName)
    {
        string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
        string[] dirs = pathEnv.Split(';');
        for (int i = 0; i < dirs.Length; i++)
        {
            string dir = dirs[i].Trim();
            if (dir.Length == 0)
            {
                continue;
            }
            string candidate = Path.Combine(dir, fileName);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }
}
