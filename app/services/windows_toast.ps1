param([Parameter(Mandatory=$true)][string]$RequestFile)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  $request=Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
  $appId=[string]$request.appid
  if ($request.operation -eq 'register') {
    if ($appId -notmatch '^[A-Za-z0-9.\-]+$') {throw 'Invalid application identity.'}
    $shortcutPath=Join-Path ([Environment]::GetFolderPath('ApplicationData')) ('Microsoft\Windows\Start Menu\Programs\'+$appId+'.lnk')
    $shell=New-Object -ComObject WScript.Shell
    $shortcut=$shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath=[string]$request.python
    $shortcut.Arguments=[string]$request.launch_args
    $shortcut.WorkingDirectory=[string]$request.root
    if ($request.icon) {$shortcut.IconLocation=[string]$request.icon+',0'}
    $shortcut.Description='Orange Notes: local notes and reminders'
    $shortcut.Save()
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class OrangeShortcut {
 [StructLayout(LayoutKind.Sequential)] public struct Key {public Guid fmtid;public uint pid;}
 [StructLayout(LayoutKind.Explicit,Size=24)] public struct Value {
  [FieldOffset(0)] public ushort vt;
  [FieldOffset(8)] public IntPtr pointer;
 }
 [ComImport,Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 public interface Store {
  void GetCount(out uint count);
  void GetAt(uint index,out Key key);
  void GetValue(ref Key key,out Value value);
  void SetValue(ref Key key,ref Value value);
  void Commit();
 }
 [DllImport("shell32.dll",CharSet=CharSet.Unicode,PreserveSig=false)]
 static extern void SHGetPropertyStoreFromParsingName(string path,IntPtr context,uint flags,ref Guid iid,out Store store);
 public static void SetIdentity(string path,string appid) {
  Guid iid=typeof(Store).GUID;Store store;
  SHGetPropertyStoreFromParsingName(path,IntPtr.Zero,2,ref iid,out store);
  Key key=new Key {fmtid=new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),pid=5};
  Value value=new Value {vt=31,pointer=Marshal.StringToCoTaskMemUni(appid)};
  try {
   store.SetValue(ref key,ref value);
   Marshal.FreeCoTaskMem(value.pointer);value.pointer=IntPtr.Zero;
   key.pid=26;
   value.vt=72;value.pointer=Marshal.AllocCoTaskMem(16);
   Marshal.StructureToPtr(new Guid("4DCDA7D4-FF50-4CA5-8FF0-94D134B33CEE"),value.pointer,false);
   store.SetValue(ref key,ref value);store.Commit();
  }
  finally {if(value.pointer!=IntPtr.Zero)Marshal.FreeCoTaskMem(value.pointer);Marshal.ReleaseComObject(store);}
 }
}
'@
    [OrangeShortcut]::SetIdentity($shortcutPath,$appId)
    $identity='HKCU:\Software\Classes\AppUserModelId\'+$appId
    New-Item -Path $identity -Force | Out-Null
    New-ItemProperty -Path $identity -Name 'DisplayName' -Value 'Orange Notes' -PropertyType String -Force | Out-Null
    if ($request.register_protocol -ne $false) {
    $protocol='HKCU:\Software\Classes\orange-notes'
    New-Item -Path $protocol -Force | Out-Null
    Set-Item -Path $protocol -Value 'URL:Orange Notes notification'
    New-ItemProperty -Path $protocol -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
    New-Item -Path ($protocol+'\shell\open\command') -Force | Out-Null
    $command=[string]$request.protocol_command
    Set-Item -Path ($protocol+'\shell\open\command') -Value $command
    }
    @{ok=$true;appid=$appId;shortcut=$shortcutPath;protocolRegistered=($request.register_protocol -ne $false)} | ConvertTo-Json -Compress
    exit 0
  }
  [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
  [Windows.UI.Notifications.ToastNotifier,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
  [Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
  [Windows.UI.Notifications.NotificationSetting,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] | Out-Null
  if ($request.operation -eq 'status') {
    $notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
    @{ok=$true;setting=$notifier.Setting.ToString()} | ConvertTo-Json -Compress
  } elseif ($request.operation -eq 'history') {
    $items=@([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory($appId) | ForEach-Object {@{tag=$_.Tag;group=$_.Group;xml=$_.Content.GetXml()}})
    @{ok=$true;items=$items} | ConvertTo-Json -Depth 5 -Compress
  } elseif ($request.operation -eq 'remove') {
    [Windows.UI.Notifications.ToastNotificationManager]::History.Remove([string]$request.tag,[string]$request.group,$appId)
    @{ok=$true} | ConvertTo-Json -Compress
  } elseif ($request.operation -eq 'show') {
    $notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
    $setting='Unknown'
    if ($null -ne $notifier.Setting) {$setting=$notifier.Setting.ToString()}
    if ($setting -ne 'Enabled' -and $setting -ne 'Unknown') {throw ('Windows notifications unavailable: '+$setting)}
    $xml=New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml([string]$request.xml)
    $toast=[Windows.UI.Notifications.ToastNotification]::new($xml)
    $toast.Tag=[string]$request.tag
    $toast.Group=[string]$request.group
    $notifier.Show($toast)
    $matched=@()
    for ($i=0;$i -lt 5;$i++) {
      $matched=@([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory($appId) | Where-Object {$_.Tag -eq $toast.Tag -and $_.Group -eq $toast.Group})
      if ($matched.Count -gt 0) {break}
      Start-Sleep -Milliseconds 100
    }
    $actualXml=''
    if ($matched.Count) {$actualXml=$matched[0].Content.GetXml()}
    @{ok=$true;submitted=$true;historyVerified=($matched.Count -gt 0);tag=$toast.Tag;group=$toast.Group;setting=$setting;xml=$actualXml} | ConvertTo-Json -Depth 5 -Compress
  } else {throw 'Unknown notification operation.'}
} catch {
  @{ok=$false;error=($_.Exception.Message+' (line '+$_.InvocationInfo.ScriptLineNumber+')')} | ConvertTo-Json -Compress
  exit 1
}
