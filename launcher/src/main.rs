//! Lumina.exe — hızlı açılış başlatıcısı.
//!
//! Eski tek dosyalık (PyInstaller onefile) exe her açılışta içindeki ~380 MB'ı %TEMP%'e açıp kapanınca
//! siliyordu: ilk kare ~2 sn bunun yüzünden gecikiyordu. Bu başlatıcı uygulamanın klasör derlemesini
//! (onedir) sonuna eklenmiş bir zip olarak taşır; her SÜRÜM için yalnızca bir kez
//! `%LOCALAPPDATA%\Lumina\runtime\<anahtar>\` altına açar, sonraki açılışlarda doğrudan oradan başlatır.
//!
//! Exe düzeni: [başlatıcı][payload.zip][iz: ofset u64, uzunluk u64, anahtar 16 bayt, sihir 8 bayt].
//! Anahtar = payload'ın SHA-256 özetinin ilk 16 hex hanesi (derleme betiği yazar): içerik değişince yeni
//! klasör açılır, eski sürümlerin klasörleri sonraki açılışta silinir.
//!
//! Açılan klasör her açılışta bildirim dosyasındaki (`.lumina-ok`: boyut + yol) her dosyanın boyutuyla
//! denetlenir (~250 dosya, birkaç ms): eksik ya da bozulmuş (ör. antivirüs karantinası) bir kurulum
//! yeniden açılır. Uygulamaya kendi yolu `LUMINA_LAUNCHER` ile bildirilir: "Birlikte aç" kaydı, görev
//! çubuğu sabitlemesi ve güncelleyici açılmış kopyayı değil bu exe'yi göstersin.
//!
//! Güncelleme: çalışan Lumina bu exe'yi yeniden adlandırıp yenisini yerine koyar ve yeni başlatıcıyı
//! `--wait-pid <eski süreç>` ile açar. Başlatıcı o süreç çıkana dek (en çok 15 sn) bekler, SONRA uygulamayı açıp eski
//! sürümlerin klasörlerini siler (çalışan eski süreçten klasörünü silmesin); `--wait-pid N` çiftini uygulamaya iletmez.
//! Bu güncelleme sonrası açılışta eski klasör hemen silinemezse (eski sürecin dosyaları çıkıştan sonra kısa süre kilitli
//! kalabilir) birkaç yüz ms aralıkla yeniden denenir; normal açılışlarda yeniden deneme yoktur.

#![windows_subsystem = "windows"]

use std::ffi::{OsStr, OsString};
use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime};

const MAGIC: &[u8; 8] = b"LUMRT\x00\x01\x00";
const TRAILER_LEN: u64 = 8 + 8 + 16 + 8;
const MANIFEST: &str = ".lumina-ok";
const APP_EXE: &str = "Lumina.exe";
/// Başka bir başlatıcının sürmekte olan açma işinin geçici klasörüne bu kadar süre dokunulmaz.
const STALE_TMP: Duration = Duration::from_secs(600);
/// `--wait-pid N`: güncellemeyi kuran eski sürecin çıkışı en çok bu kadar beklenir.
const WAIT_PID_TIMEOUT: Duration = Duration::from_secs(15);
/// Güncelleme sonrası açılışta eski sürecin dosyaları (exe/dll eşlemeleri, antivirüs taraması) çıkıştan hemen sonra birkaç yüz
/// ms daha kilitli kalabilir: eski sürüm klasörü silinemeyen kalırsa bu kadar kez, bu aralıkla yeniden denenir (toplam ~1,5 sn).
const CLEANUP_RETRIES: u32 = 5;
const CLEANUP_RETRY_DELAY: Duration = Duration::from_millis(300);

#[derive(Debug, Clone, PartialEq)]
struct Trailer {
    offset: u64,
    len: u64,
    key: String,
}

fn main() {
    let result = std::env::current_exe().map_err(|e| e.to_string()).and_then(|exe| run(&exe));
    if let Err(e) = result {
        message_box(&format!("Lumina başlatılamadı.\nLumina could not start.\n\n{e}"));
        std::process::exit(1);
    }
}

fn run(exe: &Path) -> Result<(), String> {
    run_with(exe, &runtime_root()?, std::env::args_os().skip(1).collect())
}

fn run_with(exe: &Path, root: &Path, args: Vec<OsString>) -> Result<(), String> {
    let (wait_pid, child_args) = split_wait_pid(args);
    let trailer = read_trailer(exe).map_err(|e| format!("Bozuk ya da eksik dosya: {e}"))?;
    let dir = root.join(&trailer.key);
    if !verify(&dir) {
        extract(exe, &trailer, root, &dir).map_err(|e| format!("Açılamadı ({}): {e}", dir.display()))?;
    }
    // Güncelleme sonrası açılış: eski süreç çıkana dek bekle. Açma ve eski klasörleri silme işi ondan SONRA gelir.
    if let Some(pid) = wait_pid {
        wait_for_pid(pid, WAIT_PID_TIMEOUT);
    }
    let child = Command::new(dir.join(APP_EXE))
        .args(&child_args)
        .env("LUMINA_LAUNCHER", exe)
        .spawn()
        .map_err(|e| format!("{}: {e}", dir.join(APP_EXE).display()))?;
    // Başlatıcı birazdan kapanır: penceresini öne getirme hakkını uygulamaya devret.
    allow_foreground(child.id());
    if wait_pid.is_some() {
        // Güncelleme sonrası açılış: eski süreç az önce çıktı, klasörünün dosyaları birkaç yüz ms daha kilitli olabilir (~380 MB
        // bir sonraki açılışa kalmasın). Normal açılışlarda yeniden deneme YOK: açık duran eski sürüm penceresi için bekletmeyiz.
        cleanup_old_retrying(root, &trailer.key, APP_EXE, CLEANUP_RETRIES, CLEANUP_RETRY_DELAY);
    } else {
        cleanup_old(root, &trailer.key);
    }
    Ok(())
}

/// `--wait-pid N` (ya da `--wait-pid=N`) çiftini komut satırından ayırır: (beklenecek süreç, uygulamaya iletilecek argümanlar).
/// Değer sayı değilse (ör. bir dosya yolu) yutulmaz, yalnızca bayrak atılır.
fn split_wait_pid(args: impl IntoIterator<Item = OsString>) -> (Option<u32>, Vec<OsString>) {
    let mut pid = None;
    let mut rest = Vec::new();
    let mut it = args.into_iter().peekable();
    while let Some(arg) = it.next() {
        if arg == "--wait-pid" {
            let value = it.peek().and_then(|v| v.to_str()).and_then(|v| v.parse::<u32>().ok());
            if let Some(n) = value {
                it.next();
                if pid.is_none() {
                    pid = Some(n);
                }
            }
        } else if let Some(v) = arg.to_str().and_then(|a| a.strip_prefix("--wait-pid=")) {
            if let (None, Ok(n)) = (pid, v.parse::<u32>()) {
                pid = Some(n);
            }
        } else {
            rest.push(arg);
        }
    }
    (pid, rest)
}

fn runtime_root() -> Result<PathBuf, String> {
    // Testler / duman testi kendi klasörünü verir (kullanıcınınkine dokunulmasın).
    if let Some(dir) = std::env::var_os("LUMINA_RUNTIME_DIR").filter(|d| !d.is_empty()) {
        return Ok(PathBuf::from(dir));
    }
    let base = std::env::var_os("LOCALAPPDATA").filter(|d| !d.is_empty()).ok_or("LOCALAPPDATA tanımsız")?;
    Ok(PathBuf::from(base).join("Lumina").join("runtime"))
}

fn read_trailer(exe: &Path) -> io::Result<Trailer> {
    let mut f = File::open(exe)?;
    let size = f.metadata()?.len();
    if size < TRAILER_LEN {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "iz yok"));
    }
    f.seek(SeekFrom::Start(size - TRAILER_LEN))?;
    let mut t = [0u8; TRAILER_LEN as usize];
    f.read_exact(&mut t)?;
    parse_trailer(&t, size)
}

fn parse_trailer(t: &[u8], file_size: u64) -> io::Result<Trailer> {
    let bad = |what: &str| io::Error::new(io::ErrorKind::InvalidData, what.to_string());
    if t.len() != TRAILER_LEN as usize || &t[32..40] != MAGIC {
        return Err(bad("iz imzası yok"));
    }
    let offset = u64::from_le_bytes(t[0..8].try_into().unwrap());
    let len = u64::from_le_bytes(t[8..16].try_into().unwrap());
    let key = std::str::from_utf8(&t[16..32]).map_err(|_| bad("anahtar"))?.to_string();
    if !key.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(bad("anahtar"));
    }
    if offset.checked_add(len).is_none_or(|end| end > file_size - TRAILER_LEN) {
        return Err(bad("payload sınırları"));
    }
    Ok(Trailer { offset, len, key })
}

/// Bildirimdeki her dosya yerinde ve boyutu doğru mu.
fn verify(dir: &Path) -> bool {
    let Ok(text) = fs::read_to_string(dir.join(MANIFEST)) else { return false };
    let mut any = false;
    for line in text.lines().filter(|l| !l.is_empty()) {
        let Some((size, rel)) = line.split_once('\t') else { return false };
        let Ok(size) = size.parse::<u64>() else { return false };
        match fs::metadata(dir.join(rel)) {
            Ok(m) if m.is_file() && m.len() == size => any = true,
            _ => return false,
        }
    }
    any && dir.join(APP_EXE).is_file()
}

/// Exe'nin içindeki zip'i okuyan pencere (konumsal okuma: her iş parçacığı kendi dosya tanıtıcısıyla).
struct Slice {
    f: File,
    base: u64,
    len: u64,
    pos: u64,
}

impl Read for Slice {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let left = self.len.saturating_sub(self.pos);
        if left == 0 || buf.is_empty() {
            return Ok(0);
        }
        let n = buf.len().min(left as usize);
        let got = read_at(&self.f, &mut buf[..n], self.base + self.pos)?;
        self.pos += got as u64;
        Ok(got)
    }
}

impl Seek for Slice {
    fn seek(&mut self, to: SeekFrom) -> io::Result<u64> {
        let next = match to {
            SeekFrom::Start(p) => p as i128,
            SeekFrom::End(d) => self.len as i128 + d as i128,
            SeekFrom::Current(d) => self.pos as i128 + d as i128,
        };
        if next < 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "negatif konum"));
        }
        self.pos = next as u64;
        Ok(self.pos)
    }
}

#[cfg(windows)]
fn read_at(f: &File, buf: &mut [u8], offset: u64) -> io::Result<usize> {
    std::os::windows::fs::FileExt::seek_read(f, buf, offset)
}

#[cfg(not(windows))]
fn read_at(f: &File, buf: &mut [u8], offset: u64) -> io::Result<usize> {
    std::os::unix::fs::FileExt::read_at(f, buf, offset)
}

fn open_payload(exe: &Path, t: &Trailer) -> io::Result<zip::ZipArchive<BufReader<Slice>>> {
    let slice = Slice { f: File::open(exe)?, base: t.offset, len: t.len, pos: 0 };
    zip::ZipArchive::new(BufReader::with_capacity(256 * 1024, slice)).map_err(io::Error::other)
}

/// Zip'i geçici bir klasöre açar (birkaç iş parçacığıyla), bildirimi yazar ve klasörü yerine taşır.
fn extract(exe: &Path, t: &Trailer, root: &Path, dir: &Path) -> io::Result<()> {
    fs::create_dir_all(root)?;
    let tmp = root.join(format!("{}.tmp{}", t.key, std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    fs::create_dir_all(&tmp)?;
    let result = extract_into(exe, t, &tmp).and_then(|manifest| {
        fs::write(tmp.join(MANIFEST), manifest)?;
        place(&tmp, dir)
    });
    if result.is_err() {
        let _ = fs::remove_dir_all(&tmp);
    }
    result
}

fn extract_into(exe: &Path, t: &Trailer, out: &Path) -> io::Result<String> {
    let count = open_payload(exe, t)?.len();
    let workers = std::thread::available_parallelism().map_or(2, |n| n.get()).clamp(1, 4).min(count.max(1));
    let parts: Vec<io::Result<Vec<(u64, String)>>> = std::thread::scope(|s| {
        let handles: Vec<_> = (0..workers)
            .map(|w| {
                s.spawn(move || -> io::Result<Vec<(u64, String)>> {
                    let mut zip = open_payload(exe, t)?;
                    let mut done = Vec::new();
                    for i in (w..count).step_by(workers) {
                        let mut entry = zip.by_index(i).map_err(io::Error::other)?;
                        // Zip içinden klasör dışına yazma (../) olmasın.
                        let Some(rel) = entry.enclosed_name() else {
                            return Err(io::Error::new(io::ErrorKind::InvalidData, "güvensiz yol"));
                        };
                        let path = out.join(&rel);
                        if entry.is_dir() {
                            fs::create_dir_all(&path)?;
                            continue;
                        }
                        if let Some(parent) = path.parent() {
                            fs::create_dir_all(parent)?;
                        }
                        let mut w = BufWriter::with_capacity(256 * 1024, File::create(&path)?);
                        let size = io::copy(&mut entry, &mut w)?;
                        w.flush()?;
                        done.push((size, rel.to_string_lossy().replace('/', "\\")));
                    }
                    Ok(done)
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap_or_else(|_| Err(io::Error::other("iş parçacığı çöktü")))).collect()
    });
    let mut manifest = String::new();
    for part in parts {
        for (size, rel) in part? {
            manifest.push_str(&format!("{size}\t{rel}\n"));
        }
    }
    Ok(manifest)
}

/// Açılan klasörü yerine koyar. Aynı anda başka bir başlatıcı da açtıysa onunki kullanılır.
fn place(tmp: &Path, dir: &Path) -> io::Result<()> {
    if fs::rename(tmp, dir).is_ok() {
        return Ok(());
    }
    if verify(dir) {
        let _ = fs::remove_dir_all(tmp);
        return Ok(());
    }
    // Bozuk eski kopya: kenara al (içinden açık dosya varsa silinemeyebilir), yenisini koy.
    if dir.exists() {
        let aside = dir.with_extension(format!("old{}", std::process::id()));
        fs::rename(dir, &aside)?;
        let _ = fs::remove_dir_all(&aside);
    }
    fs::rename(tmp, dir)
}

/// Eski sürümlerin klasörleri ve yarım kalmış açma işleri. Kullanımdaki (açık) bir eski sürüm silinemez;
/// bir sonraki açılışta yeniden denenir.
fn cleanup_old(root: &Path, key: &str) -> usize {
    cleanup_old_named(root, key, APP_EXE)
}

/// Eski sürüm klasörlerini siler; SİLİNEMEYEN (kullanımda ya da dosyaları kilitli) klasör sayısını döndürür.
fn cleanup_old_named(root: &Path, key: &str, exe_name: &str) -> usize {
    let Ok(entries) = fs::read_dir(root) else { return 0 };
    let mut left = 0;
    for e in entries.flatten() {
        let name = e.file_name();
        let Some(name) = name.to_str() else { continue };
        if name == key || !is_runtime_dir_name(name) {
            continue;
        }
        if name.starts_with(key) && is_recent(&e.path()) {
            continue; // aynı sürümü şu an açan başka bir başlatıcının geçici klasörü
        }
        if in_use(&e.path().join(exe_name)) {
            left += 1; // eski sürümden çalışan başka bir Lumina penceresi var: klasörü kısmen silinip bozulmasın
            continue;
        }
        let _ = fs::remove_dir_all(e.path());
        if e.path().exists() {
            left += 1; // bir dosya kilitliydi (antivirüs, az önce çıkan süreç): sonra yeniden denenir
        }
    }
    left
}

/// `cleanup_old_named`; silinemeyen klasör kalırsa `retries` kez, `delay` arayla yeniden dener. true: hepsi silindi.
/// Yalnızca güncelleme sonrası açılışta kullanılır (eski süreç az önce çıktı ve kilitleri kısa süre sonra düşer).
fn cleanup_old_retrying(root: &Path, key: &str, exe_name: &str, retries: u32, delay: Duration) -> bool {
    for attempt in 0..=retries {
        if cleanup_old_named(root, key, exe_name) == 0 {
            return true;
        }
        if attempt < retries {
            std::thread::sleep(delay);
        }
    }
    false
}

/// Uygulama exe'si şu an çalışıyor mu? Çalışan bir exe yazmak için açılamaz; dosya yoksa kullanımda değildir.
fn in_use(exe: &Path) -> bool {
    match fs::OpenOptions::new().write(true).open(exe) {
        Ok(_) => false,
        Err(e) => e.kind() != io::ErrorKind::NotFound,
    }
}

fn is_runtime_dir_name(name: &str) -> bool {
    let hex = |s: &str| s.len() == 16 && s.bytes().all(|b| b.is_ascii_hexdigit());
    match name.split_once('.') {
        None => hex(name),
        Some((k, rest)) => hex(k) && (rest.starts_with("tmp") || rest.starts_with("old")),
    }
}

fn is_recent(path: &Path) -> bool {
    fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| SystemTime::now().duration_since(t).ok())
        .is_some_and(|age| age < STALE_TMP)
}

fn wide(s: &OsStr) -> Vec<u16> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        s.encode_wide().chain(Some(0)).collect()
    }
    #[cfg(not(windows))]
    {
        s.to_string_lossy().encode_utf16().chain(Some(0)).collect()
    }
}

#[cfg(windows)]
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(hwnd: *mut core::ffi::c_void, text: *const u16, caption: *const u16, utype: u32) -> i32;
    fn AllowSetForegroundWindow(pid: u32) -> i32;
}

/// Süreç `pid` çıkana dek (en çok `timeout`) bekler. Süreç yoksa ya da açılamıyorsa (zaten çıkmış) hemen döner.
/// true: süreç çıktı (ya da yok); false: zaman aşımı.
#[cfg(windows)]
fn wait_for_pid(pid: u32, timeout: Duration) -> bool {
    const SYNCHRONIZE: u32 = 0x0010_0000;
    if pid == std::process::id() {
        return true; // kendimizi beklemeyiz
    }
    unsafe {
        let handle = OpenProcess(SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return true;
        }
        let ms = timeout.as_millis().min(u128::from(u32::MAX - 1)) as u32;
        let done = WaitForSingleObject(handle, ms) == 0; // WAIT_OBJECT_0
        CloseHandle(handle);
        done
    }
}

#[cfg(not(windows))]
fn wait_for_pid(pid: u32, timeout: Duration) -> bool {
    let proc_dir = PathBuf::from(format!("/proc/{pid}"));
    let start = std::time::Instant::now();
    while proc_dir.exists() {
        if start.elapsed() >= timeout {
            return false;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    true
}

fn message_box(text: &str) {
    #[cfg(windows)]
    unsafe {
        const MB_ICONERROR: u32 = 0x10;
        let t = wide(&OsString::from(text));
        let c = wide(OsStr::new("Lumina"));
        MessageBoxW(std::ptr::null_mut(), t.as_ptr(), c.as_ptr(), MB_ICONERROR);
    }
    #[cfg(not(windows))]
    eprintln!("{text}");
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn OpenProcess(desired_access: u32, inherit_handle: i32, process_id: u32) -> *mut core::ffi::c_void;
    fn WaitForSingleObject(handle: *mut core::ffi::c_void, milliseconds: u32) -> u32;
    fn CloseHandle(handle: *mut core::ffi::c_void) -> i32;
}

fn allow_foreground(pid: u32) {
    #[cfg(windows)]
    unsafe {
        AllowSetForegroundWindow(pid);
    }
    #[cfg(not(windows))]
    let _ = pid;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use std::process::Stdio;
    use std::time::Instant;

    fn temp_dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lumina-launcher-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    /// A fake exe: some "program" bytes, a zip with a few files, the trailer.
    fn fake_exe(dir: &Path, key: &str, files: &[(&str, &[u8])]) -> (PathBuf, Trailer) {
        let mut zip_bytes = Vec::new();
        {
            let mut w = zip::ZipWriter::new(Cursor::new(&mut zip_bytes));
            let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
            for (name, data) in files {
                w.start_file(*name, opts).unwrap();
                w.write_all(data).unwrap();
            }
            w.finish().unwrap();
        }
        let program = vec![0x4Du8; 5000];
        let mut exe = program.clone();
        exe.extend_from_slice(&zip_bytes);
        let t = Trailer { offset: program.len() as u64, len: zip_bytes.len() as u64, key: key.to_string() };
        exe.extend_from_slice(&t.offset.to_le_bytes());
        exe.extend_from_slice(&t.len.to_le_bytes());
        exe.extend_from_slice(key.as_bytes());
        exe.extend_from_slice(MAGIC);
        let path = dir.join("Lumina.exe");
        fs::write(&path, exe).unwrap();
        (path, t)
    }

    const KEY: &str = "0123456789abcdef";

    #[test]
    fn trailer_round_trips_and_rejects_garbage() {
        let d = temp_dir("trailer");
        let (exe, t) = fake_exe(&d, KEY, &[("Lumina.exe", b"inner")]);
        assert_eq!(read_trailer(&exe).unwrap(), t);
        let plain = d.join("plain.exe");
        fs::write(&plain, vec![0u8; 100]).unwrap();
        assert!(read_trailer(&plain).is_err(), "an exe without a payload is refused");
        let mut bad = fs::read(&exe).unwrap();
        let n = bad.len();
        bad[n - 24] = b'z'; // not hex
        assert!(parse_trailer(&bad[n - 40..], n as u64).is_err());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn extracts_once_verifies_and_replaces_a_damaged_copy() {
        let d = temp_dir("extract");
        let big = vec![7u8; 300_000];
        let (exe, t) = fake_exe(
            &d,
            KEY,
            &[("Lumina.exe", b"inner exe"), ("_internal/python312.dll", &big), ("_internal/a/b/c.pyd", b"c"), ("x.txt", b"xx")],
        );
        let root = d.join("runtime");
        let dir = root.join(KEY);
        assert!(!verify(&dir));
        extract(&exe, &t, &root, &dir).unwrap();
        assert!(verify(&dir));
        assert_eq!(fs::read(dir.join("_internal").join("python312.dll")).unwrap().len(), 300_000);
        assert_eq!(fs::read(dir.join("_internal/a/b/c.pyd")).unwrap(), b"c");
        let manifest = fs::read_to_string(dir.join(MANIFEST)).unwrap();
        assert_eq!(manifest.lines().count(), 4);
        assert!(fs::read_dir(&root).unwrap().count() == 1, "no temporary folder left behind");

        // A file damaged (or deleted by an antivirus): the check fails and a new extraction repairs it.
        fs::write(dir.join("x.txt"), b"x").unwrap();
        assert!(!verify(&dir));
        extract(&exe, &t, &root, &dir).unwrap();
        assert!(verify(&dir));
        assert_eq!(fs::read(dir.join("x.txt")).unwrap(), b"xx");
        fs::remove_file(dir.join("_internal/a/b/c.pyd")).unwrap();
        assert!(!verify(&dir));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn a_zip_entry_that_escapes_the_folder_is_refused() {
        let d = temp_dir("slip");
        let (exe, t) = fake_exe(&d, KEY, &[("Lumina.exe", b"x"), ("../evil.txt", b"no")]);
        let root = d.join("runtime");
        assert!(extract(&exe, &t, &root, &root.join(KEY)).is_err());
        assert!(!root.join("evil.txt").exists() && !d.join("evil.txt").exists());
        assert!(!root.join(KEY).exists());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn old_versions_and_abandoned_extractions_are_removed_but_nothing_else() {
        let d = temp_dir("cleanup");
        for name in [KEY, "fedcba9876543210", "fedcba9876543210.tmp42", "keep-me", "0123456789abcdef.tmp7"] {
            fs::create_dir_all(d.join(name)).unwrap();
        }
        fs::write(d.join("notes.txt"), b"x").unwrap();
        cleanup_old(&d, KEY);
        let mut left: Vec<String> = fs::read_dir(&d).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
        left.sort();
        // Our own version stays, and so does a fresh temporary folder of it (another launcher may be extracting).
        assert_eq!(left, vec![KEY.to_string(), "0123456789abcdef.tmp7".into(), "keep-me".into(), "notes.txt".into()]);
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn runtime_dir_names() {
        assert!(is_runtime_dir_name("0123456789abcdef"));
        assert!(is_runtime_dir_name("0123456789abcdef.tmp123"));
        assert!(is_runtime_dir_name("0123456789abcdef.old9"));
        assert!(!is_runtime_dir_name("0123456789abcde"));
        assert!(!is_runtime_dir_name("settings"));
        assert!(!is_runtime_dir_name("0123456789abcdef.bak"));
    }

    fn os(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    #[test]
    fn the_wait_pid_pair_is_taken_out_and_never_reaches_the_app() {
        let (pid, rest) = split_wait_pid(os(&["--wait-pid", "123", "--updated", "1.0.0", "--show-window", r"C:\Fotoğraflar\a b.jpg"]));
        assert_eq!(pid, Some(123));
        assert_eq!(rest, os(&["--updated", "1.0.0", "--show-window", r"C:\Fotoğraflar\a b.jpg"]));
        let (pid, rest) = split_wait_pid(os(&["foto.jpg", "--wait-pid=77"]));
        assert_eq!((pid, rest), (Some(77), os(&["foto.jpg"])));
        let (pid, rest) = split_wait_pid(os(&["a.jpg", "b.jpg"]));
        assert_eq!((pid, rest), (None, os(&["a.jpg", "b.jpg"])), "without the flag nothing changes");
        let (pid, rest) = split_wait_pid(Vec::<OsString>::new());
        assert_eq!((pid, rest), (None, vec![]));
    }

    #[test]
    fn a_wait_pid_without_a_number_never_swallows_a_file_path() {
        let (pid, rest) = split_wait_pid(os(&["--wait-pid", "foto.jpg"]));
        assert_eq!((pid, rest), (None, os(&["foto.jpg"])), "only the flag is dropped");
        let (pid, rest) = split_wait_pid(os(&["foto.jpg", "--wait-pid"]));
        assert_eq!((pid, rest), (None, os(&["foto.jpg"])));
        let (pid, rest) = split_wait_pid(os(&["--wait-pid=abc", "foto.jpg"]));
        assert_eq!((pid, rest), (None, os(&["foto.jpg"])));
    }

    #[cfg(windows)]
    fn system_cmd() -> PathBuf {
        PathBuf::from(std::env::var_os("SystemRoot").unwrap_or_else(|| OsString::from(r"C:\Windows"))).join("System32").join("cmd.exe")
    }

    /// A harmless process that exits after `seconds` seconds (opens no console window).
    #[cfg(windows)]
    fn sleeper(seconds: u32) -> std::process::Child {
        Command::new(system_cmd())
            .args(["/C", &format!("ping -n {} 127.0.0.1 >nul", seconds + 1)])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap()
    }

    #[cfg(windows)]
    fn wait_until(mut cond: impl FnMut() -> bool) -> bool {
        for _ in 0..40 {
            if cond() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        cond()
    }

    #[cfg(windows)]
    #[test]
    fn waits_for_the_process_to_exit_and_does_not_wait_for_one_that_is_gone() {
        let mut child = sleeper(1);
        let t = Instant::now();
        assert!(wait_for_pid(child.id(), Duration::from_secs(30)), "the process exited");
        assert!(t.elapsed() >= Duration::from_millis(400), "it really waited");
        child.wait().unwrap();
        let t = Instant::now();
        assert!(wait_for_pid(u32::MAX - 7, Duration::from_secs(30)), "a process that does not exist is not waited for");
        assert!(t.elapsed() < Duration::from_secs(3));
        assert!(wait_for_pid(std::process::id(), Duration::from_secs(30)), "never wait for ourselves");
    }

    #[cfg(windows)]
    #[test]
    fn gives_up_when_the_process_outlives_the_timeout() {
        let mut child = sleeper(8);
        let t = Instant::now();
        assert!(!wait_for_pid(child.id(), Duration::from_millis(300)));
        assert!(t.elapsed() < Duration::from_secs(5));
        child.kill().unwrap();
        child.wait().unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn a_running_exe_counts_as_in_use() {
        let d = temp_dir("inuse");
        let probe = d.join("LuminaProbe.exe");
        fs::copy(system_cmd(), &probe).unwrap();
        assert!(!in_use(&probe), "an idle exe can be opened for writing");
        assert!(!in_use(&d.join("yok.exe")), "a missing file is not in use");
        let mut child = Command::new(&probe)
            .args(["/C", "ping -n 4 127.0.0.1 >nul"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        assert!(in_use(&probe), "a running image cannot be opened for writing");
        child.wait().unwrap();
        assert!(wait_until(|| !in_use(&probe)), "free again once the process is gone");
        let _ = fs::remove_dir_all(&d);
    }

    #[cfg(windows)]
    #[test]
    fn a_version_folder_whose_app_is_still_running_is_not_cleaned() {
        // Another Lumina window may still run from an old version while the new one starts: its folder must not be half-deleted.
        let d = temp_dir("busy");
        let busy = d.join("fedcba9876543210");
        let idle = d.join("aaaaaaaaaaaaaaaa");
        for dir in [&busy, &idle] {
            fs::create_dir_all(dir).unwrap();
            fs::write(dir.join("data.txt"), b"x").unwrap();
        }
        fs::copy(system_cmd(), busy.join("LuminaProbe.exe")).unwrap();
        let mut child = Command::new(busy.join("LuminaProbe.exe"))
            .args(["/C", "ping -n 4 127.0.0.1 >nul"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        cleanup_old_named(&d, KEY, "LuminaProbe.exe");
        assert!(busy.join("data.txt").exists(), "the folder of a running app is left alone");
        assert!(!idle.exists(), "an unused old version is removed");
        child.wait().unwrap();
        assert!(wait_until(|| {
            cleanup_old_named(&d, KEY, "LuminaProbe.exe");
            !busy.exists()
        }));
        let _ = fs::remove_dir_all(&d);
    }

    #[cfg(windows)]
    #[test]
    fn the_app_is_started_only_after_the_waited_process_has_exited() {
        // The real flow: the "Lumina.exe" inside the fake exe is a harmless copy of cmd.exe; opening waits for `--wait-pid`.
        let d = temp_dir("waitrun");
        let payload = fs::read(system_cmd()).unwrap();
        let (exe, _t) = fake_exe(&d, KEY, &[("Lumina.exe", &payload), ("x.txt", b"x")]);
        let root = d.join("runtime");
        let mut old_process = sleeper(2);
        let args = os(&["--wait-pid", &old_process.id().to_string(), "/C", "exit"]);
        let t = Instant::now();
        run_with(&exe, &root, args).unwrap();
        assert!(t.elapsed() >= Duration::from_millis(1200), "the new version waited for the old process: {:?}", t.elapsed());
        assert!(root.join(KEY).join(".lumina-ok").exists(), "the app was unpacked");
        old_process.wait().unwrap();
        let _ = wait_until(|| fs::remove_dir_all(&d).is_ok());
    }

    /// An old version folder whose app (a harmless copy of cmd.exe) runs `ping -n <count>`: returns (folder, running process).
    #[cfg(windows)]
    fn busy_old_folder(d: &Path, name: &str, seconds: u32) -> (PathBuf, std::process::Child) {
        let dir = d.join(name);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("data.txt"), b"x").unwrap();
        fs::copy(system_cmd(), dir.join(APP_EXE)).unwrap();
        let child = Command::new(dir.join(APP_EXE))
            .args(["/C", &format!("ping -n {} 127.0.0.1 >nul", seconds + 1)])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        (dir, child)
    }

    #[cfg(windows)]
    #[test]
    fn cleanup_reports_how_many_old_folders_it_could_not_remove() {
        let d = temp_dir("left");
        let (busy, mut child) = busy_old_folder(&d, "fedcba9876543210", 3);
        let idle = d.join("aaaaaaaaaaaaaaaa");
        fs::create_dir_all(&idle).unwrap();
        assert_eq!(cleanup_old_named(&d, KEY, APP_EXE), 1, "only the folder of the running app is left");
        assert!(busy.exists() && !idle.exists());
        child.wait().unwrap();
        assert!(wait_until(|| cleanup_old_named(&d, KEY, APP_EXE) == 0));
        assert!(!busy.exists());
        let _ = fs::remove_dir_all(&d);
    }

    #[cfg(windows)]
    #[test]
    fn retrying_cleanup_removes_a_folder_that_becomes_free_shortly_after() {
        // The update case: the old process exits a moment after the first attempt; its folder must not wait for the next launch.
        let d = temp_dir("retry");
        let (busy, mut child) = busy_old_folder(&d, "fedcba9876543210", 2);
        let t = Instant::now();
        assert!(cleanup_old_retrying(&d, KEY, APP_EXE, 40, Duration::from_millis(100)), "gave up although the process exited");
        assert!(!busy.exists(), "the old version folder is gone");
        assert!(t.elapsed() >= Duration::from_millis(500), "it really waited for the process: {:?}", t.elapsed());
        assert!(t.elapsed() < Duration::from_secs(8));
        child.wait().unwrap();
        let _ = fs::remove_dir_all(&d);
    }

    #[cfg(windows)]
    #[test]
    fn retrying_cleanup_gives_up_after_the_retries_and_never_touches_a_folder_in_use() {
        let d = temp_dir("giveup");
        let (busy, mut child) = busy_old_folder(&d, "fedcba9876543210", 8);
        let t = Instant::now();
        assert!(!cleanup_old_retrying(&d, KEY, APP_EXE, 2, Duration::from_millis(100)));
        assert!(t.elapsed() < Duration::from_secs(4), "bounded wait: {:?}", t.elapsed());
        assert!(busy.join("data.txt").exists(), "a running version's folder is left intact");
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(wait_until(|| cleanup_old_named(&d, KEY, APP_EXE) == 0));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn retrying_cleanup_returns_at_once_when_nothing_is_left() {
        let d = temp_dir("nothing");
        fs::create_dir_all(d.join(KEY)).unwrap();
        fs::create_dir_all(d.join("fedcba9876543210")).unwrap();
        let t = Instant::now();
        assert!(cleanup_old_retrying(&d, KEY, APP_EXE, 5, Duration::from_millis(500)));
        assert!(t.elapsed() < Duration::from_millis(400), "no pointless waiting: {:?}", t.elapsed());
        assert!(!d.join("fedcba9876543210").exists() && d.join(KEY).exists());
        let _ = fs::remove_dir_all(&d);
    }

    #[cfg(windows)]
    #[test]
    fn after_an_update_restart_the_old_version_folder_is_gone_when_the_launcher_returns() {
        // The real flow (K3): `--wait-pid <old process>`; the old runtime folder is removed right after it exits, not at the next launch.
        let d = temp_dir("k3");
        let payload = fs::read(system_cmd()).unwrap();
        let (exe, _t) = fake_exe(&d, KEY, &[("Lumina.exe", &payload), ("x.txt", b"x")]);
        let root = d.join("runtime");
        let (old_dir, mut old_process) = busy_old_folder(&root, "fedcba9876543210", 2);
        let args = os(&["--wait-pid", &old_process.id().to_string(), "/C", "exit"]);
        run_with(&exe, &root, args).unwrap();
        assert!(!old_dir.exists(), "the ~380 MB old version folder was removed right after the update restart");
        assert!(root.join(KEY).join(MANIFEST).exists(), "the new version stays");
        old_process.wait().unwrap();
        let _ = wait_until(|| fs::remove_dir_all(&d).is_ok());
    }

    #[cfg(windows)]
    #[test]
    fn a_normal_launch_does_not_wait_for_a_running_old_version() {
        // No `--wait-pid`: a still-open window of an older version must not delay the launcher (no retry loop).
        let d = temp_dir("normal");
        let payload = fs::read(system_cmd()).unwrap();
        let (exe, _t) = fake_exe(&d, KEY, &[("Lumina.exe", &payload), ("x.txt", b"x")]);
        let root = d.join("runtime");
        let (old_dir, mut old_process) = busy_old_folder(&root, "fedcba9876543210", 4);
        let t = Instant::now();
        run_with(&exe, &root, os(&["/C", "exit"])).unwrap();
        assert!(t.elapsed() < Duration::from_millis(1300), "launcher stayed around: {:?}", t.elapsed());
        assert!(old_dir.exists(), "the running old version's folder is untouched");
        old_process.kill().unwrap();
        old_process.wait().unwrap();
        let _ = wait_until(|| fs::remove_dir_all(&d).is_ok());
    }
}
