# 9router-manager

[English](README.md#top) | **Bahasa Indonesia**

Pengelola untuk gateway **9router**: endpoint custom (provider node), API key, dan
model, lewat menu atau command line. Tanpa dependency: Python 3.8+ dan standard library
saja, jalan di **Linux**, **macOS**, dan **Windows**.

Tool ini bicara ke **admin REST API** 9router, bukan mengedit file SQLite-nya, jadi aman
dipakai saat server sedang hidup dan routing kamu tidak terganggu.

```
9router-manager/
├── 9router-manager.py        # engine (satu file, stdlib saja)
├── 9router-manager.sh        # launcher Linux / macOS / WSL / Git Bash
├── 9router-manager.ps1       # launcher Windows PowerShell
├── 9router-manager.cmd       # launcher Windows cmd.exe
├── install.sh                # setup Linux / macOS (bikin symlink `9router-manager`)
├── install.ps1               # setup Windows (bikin shim + info PATH)
└── tests/
    ├── smoke_test.py             # 31 tes lawan mock 9router (portable)
    ├── test_platform_paths.py    # 24 tes path Windows/macOS/Linux + cek paket
    ├── mock_9router_server.py    # admin API 9router tiruan, aturan auth asli
    ├── run_all.py                # jalankan semua suite
    ├── verify_with_live_9router.py # cek end-to-end (opt-in) ke server asli
    └── smoke-test.sh             # pembungkus praktis
```

## Fungsinya

| Menu | Fungsi |
| ---- | ------ |
| 1 | **Tambah endpoint custom** - nama, prefix, tipe (openai/anthropic compatible, embedding), base URL |
| 2 | **Daftar semua** - endpoint, koneksi terikat, API key client 9router, model custom, alias, model disabled, combo, backup lokal |
| 3 | **Edit endpoint custom** - nama, prefix, api type, base URL (9router otomatis menyebarkan prefix/base URL baru ke semua koneksi yang terikat) |
| 4 | **Tambah / ganti API key** - tambah key kedua untuk satu endpoint, atau ganti nama/prioritas/status koneksi |
| 5 | **Hapus** - satu koneksi saja, atau seluruh endpoint (ikut menghapus koneksi + model custom-nya) |
| 6 | **Model** - tambah/hapus model custom, bikin alias, disable/enable model per provider |
| 7 | **API key 9router** - lihat (termask), buat (ditampilkan sekali), aktif/nonaktif, hapus |
| 8 | **Tes & generate** - tes koneksi, tes model, cetak snippet siap tempel untuk client |
| 9 | **Backup & restore** - export/import seluruh config 9router |
| I | **Info & doctor** - OS, path, versi server, metode auth yang dipakai, jumlah config |

Setiap operasi tulis selalu didahului backup otomatis, dan tidak ada yang berubah tanpa
konfirmasi kecuali kamu pakai `--yes`.

## Instalasi

**Linux / macOS / WSL**

```bash
git clone https://github.com/ywildan/9router-manager
cd 9router-manager
./install.sh              # cek python + 9router, bikin ~/.local/bin/9router-manager
./install.sh --no-link    # cek saja, tanpa symlink
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/ywildan/9router-manager
cd 9router-manager
.\install.ps1             # bikin shim di %LOCALAPPDATA%\9router-manager\bin
.\9router-manager.ps1     # atau langsung jalankan launcher-nya
.\9router-manager.cmd     # dari cmd.exe
```

Kalau execution policy memblokir: `powershell -ExecutionPolicy Bypass -File .\9router-manager.ps1`

**Yang dibutuhkan**

| Perlu | Catatan |
| ----- | ------- |
| Server 9router hidup | tool ini mengelola server yang **sedang jalan** (`npx 9router`, default `http://127.0.0.1:20128`) |
| Python 3.8+ | launcher otomatis mencari `py` / `python` / `python3` |

## Pakai

```bash
./9router-manager.sh                # menu (default)
python3 9router-manager.py info     # doctor
python3 9router-manager.py list     # semua, versi manusia
```

## Mode non-interaktif

```bash
python3 9router-manager.py --list --json
python3 9router-manager.py info --json

# endpoint custom
python3 9router-manager.py add-endpoint "Gateway Lokal" mygw https://api.example.com/v1
python3 9router-manager.py edit-endpoint mygw --url https://api.example.com/v2
python3 9router-manager.py edit-endpoint mygw --prefix mygw2 --name "GW 2"
python3 9router-manager.py delete-endpoint mygw --yes

# API key (biar tidak masuk history shell, pakai --api-key-stdin)
printf '%s' "$MY_KEY" | python3 9router-manager.py add-key mygw --api-key-stdin
python3 9router-manager.py set-key <connection-id> --api-key sk-baru --name primary
python3 9router-manager.py set-key <connection-id> --inactive
python3 9router-manager.py delete-key <connection-id> --yes
python3 9router-manager.py test-connection <connection-id> --json
python3 9router-manager.py fetch-models <connection-id>   # daftar model live dari endpoint

# model
python3 9router-manager.py add-model mygw gpt-4o-mini --name "GPT-4o mini" --vision
python3 9router-manager.py delete-model mygw gpt-4o-mini --yes
python3 9router-manager.py set-alias mygw/gpt-4o-mini mini
python3 9router-manager.py disable-model mygw gpt-4o gpt-4o-mini
python3 9router-manager.py enable-model mygw

# API key client 9router
python3 9router-manager.py add-router-key laptop
python3 9router-manager.py list-keys
python3 9router-manager.py toggle-router-key laptop --disable
python3 9router-manager.py delete-router-key laptop --yes

# backup
python3 9router-manager.py backup
python3 9router-manager.py list-backups
python3 9router-manager.py restore 9router-20261018-191539.json --yes

# snippet untuk client
python3 9router-manager.py generate mygw
```

Flag global: `--base-url`, `--port`, `--token`, `--password`, `--data-dir`,
`--manager-home`, `--timeout`, `--json`, `--yes`, `--no-backup`, `--no-color`.
Alias lama `--list`, `--info`, `--doctor`, `--menu` juga tetap jalan.

## Cara autentikasinya

Admin API 9router dilindungi. Tool mencoba berurutan:

| # | Metode | Caranya |
| - | ------ | ------- |
| 1 | Token eksplisit | `--token` atau `NINEROUTER_TOKEN` |
| 2 | **Token CLI turunan** | `sha256(machine-id + "9r-cli-auth" + auth/cli-secret)[:16]` dikirim sebagai header `x-9r-cli-token` |
| 3 | Peer token | `NINEROUTER_PEER_TOKEN` dikirim sebagai `x-9r-peer-token` (hanya berlaku kalau server di-set `NINEROUTER_PEER_TOKEN`) |
| 4 | Password dashboard | `--password` / `NINEROUTER_PASSWORD` -> `POST /api/auth/login` -> cookie sesi |

Metode 2 adalah yang dipakai CLI resmi 9router untuk perintah lokal. Jadi di mesin yang
menjalankan 9router kamu biasanya tidak perlu password sama sekali. Syaratnya
`<data-dir>/machine-id` dan `<data-dir>/auth/cli-secret` bisa dibaca. Kalau tidak ada,
pakai `--password`. Login password dibatasi 9router (5 kali gagal memicu lockout), jadi
tool ini tidak pernah mencoba password salah berulang-ulang.

## Endpoint yang dipanggil

| Aksi | HTTP |
| ---- | ---- |
| Baca | `GET /api/health`, `/api/version`, `/api/provider-nodes`, `/api/providers`, `/api/keys`, `/api/combos`, `/api/models`, `/api/models/custom`, `/api/models/disabled`, `/api/models/alias` |
| Tambah endpoint | `POST /api/provider-nodes` |
| Edit endpoint | `PUT /api/provider-nodes/{id}` (body lengkap: name + prefix + baseUrl + apiType) |
| Hapus endpoint | `DELETE /api/provider-nodes/{id}` (9router ikut menghapus koneksi + model custom-nya) |
| Tambah key | `POST /api/providers` |
| Edit/hapus key | `PUT /api/providers/{id}`, `DELETE /api/providers/{id}` |
| Tes | `POST /api/providers/{id}/test`, `POST /api/models/test` |
| Daftar model live | `GET /api/providers/{id}/models` (server yang memanggil endpoint pakai key tersimpan) |
| Model | `POST|DELETE /api/models/custom`, `PUT|DELETE /api/models/alias`, `POST|DELETE /api/models/disabled` |
| API key client | `GET|POST /api/keys`, `PUT|DELETE /api/keys/{id}` |
| Combo | `GET|POST /api/combos`, `DELETE /api/combos/{id}` |
| Backup / restore | `GET /api/settings/database`, `POST /api/settings/database` |

Diuji terhadap **9router 0.5.104**. Bentuk payload-nya didokumentasikan di
`tests/mock_9router_server.py`, yang gagal dengan cara yang sama seperti route aslinya.

## Keamanan

- **API key selalu termask** (`sk-a...9f3c`). Admin API tidak pernah mengembalikan
  kredensial tersimpan, jadi untuk tampilan tool membaca `data.sqlite` secara
  **read-only** (best effort, dilewati kalau tidak bisa). Tidak ada yang dicetak penuh
  kecuali API key client 9router yang baru dibuat, dan itu memang ditampilkan sekali.
- **Backup otomatis sebelum setiap tulis** lewat API:
  `<manager-home>/backups/9router-YYYYMMDD-HHMMSS.json`, menyimpan 20 terbaru.
  `--no-backup` untuk melewati.
- **Restore** dikirim ke `POST /api/settings/database`; config sekarang dibackup dulu.
- **Konfirmasi** di semua aksi hapus/restore; `--yes` untuk skrip.
- **Pesan error jelas**: 401 menyebut metode auth yang dipakai dan asal tokennya; server
  mati menyebut setiap URL yang dicoba.

## Path per OS

| Apa | Linux | macOS | Windows |
| --- | ----- | ----- | ------- |
| Data dir 9router | `~/.9router` | `~/.9router` | `%APPDATA%\9router` |
| Database 9router | `<data-dir>/db/data.sqlite` | sama | sama |
| CLI secret | `<data-dir>/auth/cli-secret` | sama | sama |
| Manager home | `~/.9router-manager` | sama | sama |
| Backup | `<manager-home>/backups` | sama | sama |

Catatan Windows: `~` berarti `%USERPROFILE%`. Mode file `600` hanya diterapkan di POSIX;
Windows memakai ACL dari `%APPDATA%`. Kalau 9router jalan di Docker dengan `DATA_DIR`
khusus, pakai `--data-dir` atau set `NINEROUTER_DATA_DIR`.

## Variabel environment

| Var | Fungsi |
| --- | ------ |
| `NINEROUTER_URL` / `NINEROUTER_BASE_URL` | URL server 9router. Kalau di-set, tool **tidak** fallback ke port lain, jadi salah ketik langsung kelihatan |
| `NINEROUTER_PORT` / `PORT` | port yang dicoba lebih dulu saat auto-detect |
| `NINEROUTER_TOKEN` | override token CLI turunan |
| `NINEROUTER_PASSWORD` | password dashboard (auth cadangan) |
| `NINEROUTER_PEER_TOKEN` | kirim `x-9r-peer-token` sebagai ganti token CLI |
| `NINEROUTER_DATA_DIR` / `DATA_DIR` | data dir 9router (variabel yang sama dipakai server 9router) |
| `NINEROUTER_MANAGER_HOME` | tempat `state.json` dan backup |
| `NINEROUTER_MANAGER_NO_COLOR` / `NO_COLOR` | matikan warna ANSI |
| `NINEROUTER_MANAGER_PYTHON` | paksa interpreter tertentu di launcher |

## Testing

```bash
python3 tests/run_all.py     # dua suite, ~25 detik
bash tests/smoke-test.sh     # sama, di Linux/macOS
python3 tests/smoke_test.py add   # hanya tes yang namanya memuat "add"
```

Semua tes jalan lawan mock server di `127.0.0.1` dengan data dir dan manager home
sementara, jadi config 9router asli tidak pernah tersentuh. Semuanya Python murni tanpa
shell, jadi perintah yang sama jalan di Windows: `python tests\run_all.py`.

Untuk 9router yang sedang jalan di mesin kamu (opt-in, bikin endpoint sementara lalu
menghapusnya sendiri, plus cek backup/restore):

```bash
python3 tests/verify_with_live_9router.py --allow-writes
```

Isinya 15 pengecekan: auth token turunan, tambah endpoint, ikat key, tambah model, edit
endpoint (termasuk cek 9router menyebarkan base URL baru ke koneksi terikat), generate
snippet, tes koneksi, hapus endpoint (cascade), jumlah config kembali ke kondisi awal,
dan restore yang menyisakan config identik.

Hasil terakhir (9router 0.5.104, Linux): **31 ok / 0 fail** (smoke), **24 ok / 0 fail**
(platform), **15 ok / 0 fail** (live). CI menjalankan dua suite offline di ubuntu 3.8,
ubuntu 3.12, macOS, dan Windows (3.8 dan 3.12), plus pengecekan ketiga launcher di
tiap OS.

## Batasan (jujur)

- **Koneksi OAuth read-only di sini.** Koneksi hasil login provider (misalnya langganan
  Qoder) bisa dilihat, dinonaktifkan, dan dihapus, tapi tokennya di-refresh sendiri oleh
  9router. Untuk menambah, pakai dashboard.
- **API key tersimpan tidak bisa dibaca lewat API.** Kolom key diisi dengan membaca
  `data.sqlite` read-only; kalau file itu tidak terbaca (izin volume Docker, network
  share), kolomnya jadi `(tersimpan)`.
- **Tidak ada pemisahan per project.** 9router itu satu gateway per data dir. Untuk
  isolasi, jalankan instance kedua dengan `DATA_DIR` sendiri dan kelola pakai
  `--data-dir` / `--base-url`.
- **Tidak ada sinkronisasi katalog model.** `/api/models/catalog-sync` itu operasi sisi
  server; tool ini hanya menambah/hapus model custom dan alias.
- **Clipboard** bersifat best-effort (`clip` di Windows, `pbcopy` di macOS, `wl-copy` /
  `xclip` / `xsel` di Linux) dan tidak pernah menggagalkan perintah.

## Kolaborasi dengan jcode

9router sendiri bisa menulis profil provider jcode lewat
`POST /api/cli-tools/jcode-settings`. Kalau mau manual, menu `generate` sudah mencetak
snippet-nya:

```bash
$ python3 9router-manager.py generate mygw
  base url OpenAI    http://127.0.0.1:20128/v1
  # jcode
  jcode provider add mygw --base-url http://127.0.0.1:20128/v1 --model mygw/gpt-4o-mini
  jcode --provider-profile mygw
```

Untuk sisi TOML jcode (menambah/mengganti provider di `~/.jcode/config.toml`), pakai
[jcode-manager](https://github.com/ywildan/jcode-manager). Tool ini fokus ke sisi 9router.

## Troubleshooting

**`9router server tidak terjangkau`**
Server belum jalan atau beda port: `npx 9router`, lalu cek
`curl http://127.0.0.1:20128/api/health`. Pakai `--base-url` untuk 9router remote/tunnel.

**`401 Unauthorized`**
Token turunan tidak cocok: data dir yang dipakai tool berbeda dari yang dipakai server
(`--data-dir`, `DATA_DIR`, mount Docker), atau `auth/cli-secret` tidak terbaca. Pakai
`--password` sebagai cadangan.

**`HTTP 400 ... Name is required`**
Itu perilaku versi lama. Versi sekarang selalu mengirim body lengkap saat edit endpoint.
Data tidak berubah karena permintaannya ditolak sebelum ada perubahan.

**Restore sepertinya tidak mengubah apa pun**
`POST /api/settings/database` mengganti seluruh config, jadi yang mau dipulihkan harus
ada di file itu. Cek `list-backups` dan lihat isi JSON-nya: ada `providerConnections`,
`providerNodes`, `apiKeys`, `customModels`, `modelAliases`, `combos`.

## Lisensi

MIT
