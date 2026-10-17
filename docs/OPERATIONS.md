# QRadar-Dash işletme kılavuzu

Bu kılavuz, QRadar-Dash'i kuran ve işleten SOC veya altyapı operatörü içindir. Komutlar, günlük
satırları, dosya yolları, ayar adları, HTTP durum kodları ve `doctor` kimlikleri kaynaklardaki gibi
bırakılmıştır. Çevirilmemiştir.

Dört kural her bölümde geçerlidir:

> 1. Alembic downgrade hiçbir zaman kullanılmaz. Geri alma, bir yedeğin geri yüklenmesidir.
> 2. SELinux hiçbir zaman kapatılmaz ve `:z` hiçbir zaman kullanılmaz.
> 3. Sırlar hiçbir zaman `.env` dosyasına, kabuk geçmişine, bir bilete ya da destek paketine girmez.
> 4. Bu araçta hiçbir şey QRadar'a yazmaz. Araç QRadar'a karşı salt okunurdur.

**İçindekiler**

1. [Kurulum](#kurulum)
2. [Dizin](#dizin)
3. [Yükseltme](#yükseltme)
4. [Yedek ve geri alma](#yedek-ve-geri-alma)
5. [Tanı](#tanı)
6. [Belirtiler](#belirtiler)
   (OPS-R1 ... OPS-R45; `doctor` satırındaki `runbook: OPS-R30` yazısı 30. girişi gösterir)

`install`, `status`, `logs`, `doctor`, `backup`, `restore`, `upgrade`, `rollback` ve
`support-bundle` bugün çalışır. `bundle` (çevrimdışı paket) bu sürümde yoktur. `./qradar-dash bundle`
şu satırı yazıp 64 ile çıkar:

```
'bundle' is not built yet (the offline bundle is not part of this release; use ./qradar-dash install --build from a checkout, or install by pulling from the registry).
```

`install` yardımındaki satır şudur:
`Not built in this release (use install --build, or pull from the registry).`

---

## Kurulum

Kurulum tek komuttur. Kayıt defterinden (registry) kuran olağan kurulum alt modül istemez:

```bash
git clone https://github.com/wiemBe/QRadar-Dashboard.git
cd QRadar-Dashboard
./qradar-dash install
```

`--recurse-submodules` yalnız `install --build` için gerekir: `qradar-mcp` alt modülünü çeker. Kayıt
defterinden kuran bir kurulum alt modül olmadan çalışır.

```bash
git clone --recurse-submodules https://github.com/wiemBe/QRadar-Dashboard.git
cd QRadar-Dashboard
./qradar-dash install --build
```

Betik bash ile yazılmıştır ve `docker compose` (v2) kullanır. Ana makinede Python çalıştırmaz.
Şifreleme anahtarı, imaj geldikten sonra backend imajının içinde üretilir.

Test ve yeniden çalıştırma için sorular bir dosyadan okunabilir:

```bash
./qradar-dash install --non-interactive .secrets/install.answers
```

Yanıt dosyasının izni `0600` olur. İçinde yollar ve yönetici kullanıcı adı bulunur. Parola,
şifreleme anahtarı ve QRadar token'ı bulunmaz.

`install` seçenekleri:

| Seçenek | Anlamı |
|---|---|
| `--non-interactive FILE` | Yanıtları FILE dosyasından oku (izin `0600`, sır yok). |
| `--build` | İmajları çekmek yerine bu checkout'tan derle. |
| `--offline BUNDLE` | İmajları çevrimdışı paket tar'ından yükle. |
| `--external-proxy` | Paketle gelen TLS vekilini atla; kendi vekilini sen işlet. |
| `--reconfigure` | Yanıtlar kayıtlı olsa bile soruları yeniden sor. |
| `--dry-run` | Her kontrolü çalıştır, hiçbir şey yazma, hiçbir şey değiştirme. |
| `--skip-qradar-check` | QRadar konsoluna TLS el sıkışması testini atla. |

### Operatör hesabı ve yedek dizini

Kurulum ve işletme için ayrılmış bir hesap kullan; günlük işi `root` ile yapma. Hesap, `docker`
komutunu çalıştırabilmelidir: `install`, `status`, `doctor`, `backup`, `restore`, `upgrade` ve
`rollback` tümü `docker compose` çağırır. Bu yüzden hesabı `docker` grubuna ekle.

> RHEL, Rocky ve Alma'da **`operator` zaten bir sistem hesabıdır** (uid 11, giriş yoktur,
> `/sbin/nologin`). Bu adı seçme; kendi hesabını kur, örneğin `qdops`.

```bash
sudo useradd -m -G docker qdops
sudo passwd -l qdops
```

**Yedek dizini** kurulumdan **önce** vardır ve operatör hesabına aittir. `install` dizini `0700` ile
oluşturur; üst dizin `root` sahipliyse operatör onu oluşturamaz ve ön kontrol hiçbir şey yazmadan
durur:

```
cannot create the backup directory <path>: <parent> is not writable by this account; create it first (see docs/OPERATIONS.md, Kurulum)
```

Dizin varsa ama hesap yazamıyorsa mesaj şudur:

```
the backup directory <path> is not writable by this account; give it to this account (see docs/OPERATIONS.md, Kurulum)
```

Yedek dizini git checkout'unun dışında olmalıdır. Sahipliği operatöre ver:

```bash
sudo mkdir -p /var/backups/qradar-dash
sudo chown qdops /var/backups/qradar-dash
sudo chmod 700 /var/backups/qradar-dash
```

### Ana makine boyutu

Bayt/satır değerleri ölçülmüş değildir. Planlama sabiti satır başına 1 KiB'dir. İlk haftadan sonra
`pg_database_size` ile değiştirilecek bir tahmindir. `doctor` bu boyutu yazar.

- **En az:** 4 vCPU, 8 GiB RAM, **Docker kökünde 80 GiB boş** disk. Bu ana makine, varsayılanda
  kapalı aileler kapalı bırakılmış Phase A içindir.
- **Önerilen:** 8 vCPU, 16 GiB RAM, Docker ve Postgres için 400 GiB ve yedek dizini olarak bağlanmış
  **ikinci bir dosya sistemi** (400 GiB). İkinci dosya sistemi, veri diski dolduğunda bile yedeğe
  erişmeni sağlar. Yedek dizini ile Docker veri kökü aynı aygıttaysa kurulum ön kontrolü uyarır,
  reddetmez. Aynı aygıttaki yedek hatalı bir migrasyondan kurtarır, ölü bir diskten kurtarmaz.
- Retention (saklama süresi) ayarı `retention_enabled` varsayılanda kapalıdır. Kurulum sihirbazı
  `RETENTION_ENABLED=true` yazar. Politika `apply_timescale_policies` görevi çalışana kadar
  uygulanmaz. Bu yüzden diski izle (bkz. OPS-R14).

Ana makinenin izin vermesi gereken portlar:

- Gelen: vekile `443/tcp`.
- Giden: QRadar konsolu `443/tcp`, AD `636/tcp`, `ghcr.io:443`, `github.com:443`, NTP.
- Yayımlanmaz: Postgres, Redis, MCP. API ve arayüz ana makinede yalnız loopback'te durur:
  `127.0.0.1:8000` ve `127.0.0.1:3000`. Uzak analistler yalnız `https://<ad>/` kullanır.

### Ön kontrol

Ön kontrol hiçbir şey değiştirmez. Bir kontrol başarısız olursa betik `.env` yazılmadan durur.

| Kontrol | Geçer | Başarısız / uyarı |
|---|---|---|
| Mimari | `uname -m` çıktısı `x86_64` | Aksi halde başarısız. |
| Docker | `docker version` ve `docker compose version` ikisi de çalışır | Başarısız. Compose v2 gerekir. `docker-compose` çağrılmaz. |
| Kaynak | en az 4 CPU, 8 GiB RAM, Docker kökünde 80 GiB boş, yedek dizininde 20 GiB boş | Eşiğin altında başarısız. |
| Portlar | Paketle gelen vekil kullanılıyorsa `443` boş. `127.0.0.1:3000` ve `127.0.0.1:8000` boş. | Doluysa başarısız. |
| SELinux | `getenforce` yazdırılır | Hiç değiştirilmez. `Enforcing` normal bir sonuçtur. |
| Saat | `timedatectl show` NTP eşitli der | Eşitli değilse uyarı. |
| Saat dilimi | yazdırılır | Uygulama UTC saklar. Arayüzdeki saatleri UTC olarak oku. |
| QRadar TCP | `openssl s_client -connect <host>:443 -CAfile <ca> -verify_return_error` | OPS-R3'teki AKI durumu dışındaki doğrulama hatasında başarısız. Token ve AQL göndermez. |
| Yedek dizini | var ya da oluşturulabilir, izin `0700` olacak, git checkout'unun içinde değil | Oluşturulamazsa başarısız. |

`docker-compose.override.yml` git tarafından yok sayılır. Laboratuvarda libvirt macvlan'ını bağlamak
için kullanılır. Ön kontrol bu dosyanın var olup olmadığını yazdırır, silmez. QRadar:443'e
yönlenebilen bir üretim ana makinesinin buna ihtiyacı yoktur.

### Sihirbaz

Sorular şu sırayla sorulur:

1. Genel ana makine adı. `PUBLIC_ORIGIN=https://<ad>` olarak, yol olmadan saklanır.
2. Bu ad için TLS sertifika dosyası ve özel anahtar dosyası. `.secrets/tls.crt` ve
   `.secrets/tls.key` olarak (izin `0600`) kopyalanır, sonra **ayrı** bir volume'a
   (`qradar-observability-proxy-certs`) aktarılır. QRadar sırlarının volume'una eklenmez.
3. Harici vekil, evet ya da hayır. Varsayılan hayır.
4. QRadar taban URL'si (`https://...`). Başka biçim reddedilir.
5. QRadar konsolunun FQDN'i (yalın ana makine adı), `QRADAR_CONSOLE_FQDN` için.
6. API sürümü. Varsayılan `20.0`. Laboratuvar komutu `29.0` kullanır. Sihirbaz bunu söyler, tahmin
   etmez.
7. SEC token dosyasının yolu. `.secrets/qradar.sec` olarak (izin `0600`) kopyalanır. Token kabuğa
   yazılmaz ve `.env` dosyasına girmez.
8. QRadar CA paketinin yolu. `.secrets/qradar-ca.pem` olarak kopyalanır. Paket kökü ve ara
   sertifikayı içermelidir.
9. `QRADAR_TLS_ALLOW_MISSING_AKI`, varsayılan false. Yalnız evet/hayır sorulur (bkz. OPS-R3).
10. Örnek adı. Varsayılan `qradar`.
11. Yedek dizini.
12. GHCR kullanıcı adı ve `read:packages` token dosyasının yolu. `--offline` ve `--build` için
    atlanır.
13. Acil durum (break-glass) yönetici kullanıcı adı. Parola sorulmaz, `create-admin` üretir.

Sorulmayanlar: LDAP (arayüzden yapılır), dedektör eşikleri, aile etkinleştirme, SMTP, webhook, LLM.
WAF, IPS, kural eşleşmesi toplama ve özel kurallar varsayılanda **kapalı** kalır. Catch-all ve geo
varsayılanda açıktır. Özet bunu söyler.

`.env` dosyası (izin `0600`) imaj geldikten sonra yazılır. `SECRET_KEY`, `ENCRYPTION_KEY` ve
`POSTGRES_PASSWORD` üretilir. `QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token` bir yoldur;
token değeri `.env` içinde yoktur.

### Adım sırası

1. `docker login` (GHCR token dosyası varsa).
2. İmajları çek, yükle ya da derle.
3. Fernet anahtarını üret ve `.env` yaz.
4. `./deploy/load-secrets.sh`. Betik eksik token'ı ya da CA'yı reddeder, `qradar-observability-secrets`
   volume'unu oluşturur ve dosyaları stdin üzerinden aktarır. Ana makinedeki etiketler değişmez.
5. `postgres` ve `redis` başlar, ikisi de sağlıklı olana kadar beklenir.
6. `run --rm migrate`. Sıfırdan farklı çıkış kurulumu durdurur. Betik `docker compose logs migrate`
   komutunu yazdırır.
7. Acil durum yöneticisi, migrate'ten sonra:

   ```bash
   docker compose run --rm --no-deps backend \
     python -m app.cli.users create-admin --username <name>
   ```

   Parola bir kez terminale yazdırılır. Betik onu dosyaya ya da günlüğe yazmaz.

   **`python -m app.services.seed` komutunu çalıştırma.** Boş veritabanında `mock` sağlayıcılı bir
   `default` satırı ekler. Toplama sonra bu satırdan sağlayıcı kurar ve sessizce QRadar'ı okumayı
   bırakır.

8. Konsolu konteynerin içinde kaydet; saklanan CA yolu böylece konteyner yolu olur:

   ```bash
   docker compose run --rm backend \
     python -m app.cli.qradar add \
       --name <name> \
       --url <https-url> \
       --token-file /run/secrets/qradar_sec_token \
       --ca-file /run/secrets/qradar_ca.pem \
       --api-version <version>
   ```

   `add` ada göre idempotent'tir. Bağlantı kontrolü başarısız olsa bile satır kaydedilir ve komut 1
   döner. Kurulum devam eder, özette FAIL yazar ve zaten yazdırılmış yönetici parolasını kaybetmez.
   Ortamdaki token da gereklidir: üretimde `rest` sağlayıcı token'sız açılmaz. İki kopya da gerekir.

9. Vekil (harici değilse), backend, frontend, `celery-worker` ve `celery-beat` başlar. `mcp` profili
   başlamaz.
10. Bu servislerin sağlıklı olması (migrate için 0 koduyla çıkması) beklenir. Bekleme süresi 180 sn.
11. Ana makineden `GET http://127.0.0.1:8000/api/v1/health/ready` ve
    `GET http://127.0.0.1:8000/api/v1/version`.

Bir test VM'de ilk kurulum toplam 1 dk 40 sn sürdü (QRadar erişilemezken). Bu bir söz değildir.

### Özet ve ilk oturum açma

Özet en sonda yazdırılır: URL `https://<ad>/`, oturum açma yolu `/login`, yönetici kullanıcı adı ve
"parola yukarıda yazdırıldı ve saklanmıyor" cümlesi, QRadar bağlantı sonucu, kapalı aileler (WAF,
IPS, kural eşleşmeleri, özel kurallar), yedek dizini ve bu kılavuzun yolu `docs/OPERATIONS.md`.

- Çıkış kodu 0: yığın sağlıklı ve yönetici var.
- Çıkış kodu 1: QRadar kontrolü başarısız ya da bir servis hiç sağlıklı olmadı. Parola bu çıkıştan
  önce stdout'a yazılır; başarısız QRadar kontrolü onu yutmaz.

  QRadar kontrolü başarısız olduğunda yığın ve `.qradar-dash/state.json` **yerindedir**: kurulum
  "başarısız" görünse de `status`, `upgrade` ve `rollback` çalışır. QRadar'ı düzelt ve `install`
  komutunu yeniden çalıştır (`qradar add` satırı günceller); ya da `./qradar-dash status` ile duruma
  bak. Yığın ayağa kalkmadıysa (bir servis hiç sağlıklı olmadı) `.qradar-dash/state.json` yazılmaz;
  `docker compose ps` ve `./qradar-dash logs` ile bak.

İlk oturum açmada parola değiştirmek zorunludur (`must_change_password`). Değiştirene kadar API her
başka isteğe 403 `A password change is required before any other request.` ile cevap verir. Bu bir
arıza değildir (bkz. OPS-R27). Komut satırından API'ye oturum açacaksan isteğe siteyle eşleşen bir
`Origin` başlığı ekle (`https://<ad>`); CSRF koruması eksik başlığa 403
`Cross-site request refused: the request has no Origin or Referer header.` döner (bkz. OPS-R22).
Sonra:

1. `/admin/directory` sayfasında AD kullanılıyorsa dizini ayarla (bkz. Dizin).
2. `/admin/users` sayfasında kullanıcılara rol ata (bkz. OPS-R25).
3. `./qradar-dash doctor` çalıştır.

### Yeniden çalıştırma

Mevcut `.env` ile ikinci `install`:

- `SECRET_KEY`, `ENCRYPTION_KEY` ve `POSTGRES_PASSWORD` yeniden üretilmez. `ENCRYPTION_KEY`'i
  değiştirmek saklı şifreli veriyi okunmaz yapar. `.env` içindeki `POSTGRES_PASSWORD`'i değiştirmek
  hazır bir volume'un içindeki parolayı değiştirmez.
- Kullanıcı adı zaten varsa `create-admin` çağrılmaz. Tekrar çağırmak parolayı sıfırlar ve
  oturumları iptal eder.
- `load-secrets.sh` ve `qradar add` yeniden çalışır; migrate'te Alembic zaten başta olduğundan bir
  şey yapmaz; `up -d` yeniden çalışır.

`--reconfigure` sır olmayan soruları yeniden sorar. Postgres volume'u varsa `ENCRYPTION_KEY` ve
`POSTGRES_PASSWORD` değişimini reddeder.

Yeniden çalıştırma, sitenin çalıştırdığı sürümün checkout'unda yapılır. `.qradar-dash/state.json`
varsa `install` kayıtlı sürümü değiştirmez; checkout'un `VERSION` dosyası kayıtlı sürümden farklıysa
(ya da son yükseltme başarısız kaydedilmişse, ya da durum dosyası geçersizse) hiçbir şey yazmadan
`--dry-run` ile bile reddeder:

```
install refused: this site runs 0.1.0 and this checkout is 0.1.1; use ./qradar-dash upgrade 0.1.1 to change versions, or check out v0.1.0 to re-run install (see docs/OPERATIONS.md, Yükseltme)
install refused: the last upgrade failed; run rollback or restore first
install refused: .qradar-dash/state.json field <alan> is invalid; see ./qradar-dash status
```

Sürüm değiştirmenin tek yolu `upgrade`'dir: yükseltme öncesi yedek alır ve otomatik geri alma
vardır, `install` ise ikisine de sahip değildir. Durum dosyası hiç yoksa `install` her zamanki gibi
çalışır; bu, QRadar kontrolü başarısız olan bir 0.1.0 sitesinin durum dosyasına ulaştığı yoldur.

---

## Dizin

Dizin (LDAPS / AD) ayarı kurulumda sorulmaz. Arayüzden `/admin/directory` sayfasında yapılır.

- **Test düğmesi** yapılandırmayı sınar ve yapılandırılmış bir TLS nedeni döndürür. Dizin sorunlarında
  tahmine ya da paket yakalamaya değil, bu sonuca bak.
- Test sonucu LDAP sertifikasının `not_after` tarihini de gösterir.
- Test sırasında parola gerekir. Kayıtlı bir servis hesabı yoktur ve parola boşsa soket açılmadan
  reddedilir (bkz. OPS-R40).
- Roller AD'den okunmaz. İlk başarılı oturum açma kullanıcıyı rolsüz oluşturur; bir yönetici
  `/admin/users` sayfasında rol atar (bkz. OPS-R25).
- Yapıştırdığın CA yalnız CA sertifikası olmalıdır. Özel anahtar içeren, sertifika olmayan ya da
  süresi dolmuş bir PEM reddedilir (bkz. OPS-R39).

Başarısız testin anlamı test sonucunun nedenine bağlıdır. Nedenler ve çözümleri için OPS-R23,
OPS-R24, OPS-R39 ve OPS-R40 girişlerine bak. `doctor` LDAP'a bağlanmaz; dizin yalnız Test düğmesiyle
denetlenir.

---

## Yükseltme

Önce checkout'u hedef etikete getir; ardından komutu çalıştır:

```bash
git fetch --tags
git checkout v<version>
./qradar-dash upgrade <version> [--offline]
```

Yükseltmeyi hedef sürümün betiği ve compose dosyaları yürütür; eski bir checkout, yeni imajları eski
compose dosyasıyla başlatırdı. Bu yüzden `upgrade`, `VERSION` dosyası hedefle aynı olmayan bir
checkout'u, hiçbir şey çekmeden ve durdurmadan reddeder:

```
upgrade refused: this checkout is version <checkout>, not <hedef>; run git fetch --tags && git checkout v<hedef> first (see docs/OPERATIONS.md, Yükseltme)
```

`VERSION` okunamıyorsa `<checkout>` yerine `unknown` yazılır. Kayıt defteri kurulumu submodule
gerektirmez. Sürüm notları yine hedef imajdan okunur.

`version`, başında `v` olmayan, düz SemVer'dir (`MAJOR.MINOR.PATCH`). Aksi halde betik
`upgrade version must be plain SemVer (MAJOR.MINOR.PATCH)` ile reddeder. İmaj etiketlerinde `v`
yoktur. `--offline`, çekmek yerine hedef imajların yerelde yüklü olmasını şart koşar.

`upgrade` ve `rollback`, `doctor` raporundan yalnız `db_connect` ve `migration_head` satırlarını
yazdırır; ardından `Full report: ./qradar-dash doctor` satırı gelir:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0025
Full report: ./qradar-dash doctor
```

Raporun geri kalanı (henüz yazılmamış bir beat kalp atışı, QRadar kontrolleri) yükseltmeyi ya da
geri almayı yanlış bir başarısızlık gibi gösterirdi; bu yüzden yalnız karar veren iki satır yazılır.

### Ön kontrol

Hiçbir konteyner durdurulmadan önce:

- `.qradar-dash/state.json` vardır ve geçerlidir; değilse betik geçersiz ya da eksik alanı
  adıyla bildirir (`upgrade refused: .qradar-dash/state.json field <alan> is invalid or missing`).
- `installed_version` hedeften eskidir. Aynı sürüm ve sağlıklı konteynerlerde komut çalışma
  kontrollerini geçer, `Version <sürüm> is already installed and healthy; nothing changed.`
  yazar ve 0 ile çıkar; aynı sürüm sağlıklı değilse
  `version <sürüm> is recorded but the running stack is not healthy: <neden>` ile durur.
  Daha yeni kurulu sürüm `upgrade refused: <kurulu> is newer than <hedef>; that is rollback` ile
  reddedilir.
- Hedef imaj çekilebilir ya da zaten yüklüdür. Çekme, yazıcılar durmadan **önce** olur; kayıt
  defteri hatası kesinti yaratmaz. Çekilemezse
  `upgrade refused: target images are unavailable; no container was stopped`. `--offline`
  verilmişse çekme yerine her hedef imajın yerelde olduğu doğrulanır.
- Hedef imajın sürüm notları ayrıştırılır ve `installed_version`, `min_from` değerine eşit ya da
  büyüktür. Ayrıştırılamazsa
  `upgrade refused: target release notes are missing, invalid, or for another version`; eskiyse
  `upgrade refused: <hedef> requires at least <min_from>`. Dur: imajlar çekilmiştir,
  konteynerlere dokunulmamıştır.
- Disk koruması (bkz. Yedek ve geri alma) geçer. Yedek kökü yoksa
  `upgrade refused: backup root is unavailable`.
- `doctor` `db_connect` ve `migration_head` **PASS** verir. Biri FAIL ise
  `upgrade refused: database readiness is already failing; see runbook OPS-R11`; biri hiç PASS
  bildirilmediyse `upgrade refused: doctor did not report db_connect and migration_head as PASS;
  see runbook OPS-R11`. Veritabanı **mevcut** imajın başında değilse biri elle migrate etmiştir
  ya da önceki migrate çökmüştür. Dur ve OPS-R11'e bak.
- `.env` içindeki `QRADAR_DASH_VERSION`, durum dosyasındaki `installed_version` ile aynıdır.
  Değilse `upgrade refused: .env has QRADAR_DASH_VERSION=<değer> but the state file says
  <kurulu>`.
- Önceki yükseltme `outcome=failed` kaydettiyse, `rollback` ya da başarılı bir `restore` bunu
  temizleyene kadar yeni yükseltme `upgrade refused: the last upgrade failed; run rollback or
  restore first` ile engellenir. `last_upgrade.restored_from` alanı dolu olan `failed` bir kayıt
  bu engeli kaldırır (başarılı bir `restore` o alanı yazar).

### Yedek, sonra migrasyon

1. Çalışan imaj özetleri durum dosyasına `previous_images` olarak yazılır.
2. Yazıcılar durdurulur: `backend`, `celery-worker`, `celery-beat`. `postgres` ve `redis` açık
   kalır. `frontend` ve `proxy` de durdurulur; analistler yarım çalışan bir arayüz yerine bağlantı
   hatası görür. Sebep: migrasyon `NOT NULL` sütunlar ekler ve doldurma varsayılanını kaldırır;
   eski kodla çalışan bir süreç bir sonraki ekleme işleminde hata alır.
3. Yedek alınır, nedeni `pre-upgrade`, geçici veritabanına geri yükleme testi dahil. Test
   başarısızsa yükseltme iptal edilir ve yazıcılar **eski** özetlerle yeniden başlar.
4. Redis boşaltılır (`redis-cli FLUSHALL`). Başarısızsa yazıcılar **eski** özetlerle yeniden
   başlar.
5. Yükseltme denemesi durum dosyasına yazılır: `installed_version` ve iki imaj haritası eski,
   `last_upgrade` hedefe `outcome=failed` ve az önce alınan yedekle. Bu kayıt `.env`
   düzenlenmeden ve migrate çalışmadan **önce** yapılır; buradan sonraki bir çökme ya da yeniden
   başlatma `upgrade`'i bloklar ve `rollback`'in çalışmasını sağlar. Bu yazı `.env` düzenlemesinden
   önce başarısız olursa yazıcılar **eski** özetlerle yeniden başlar.
6. Compose projesi yeni özetlere çevrilir (`.env` içindeki `QRADAR_DASH_VERSION` tek satırda
   atomik güncellenir).
7. **Yeni** backend imajıyla `run --rm migrate`.
8. Migrate 0 ile çıkarsa: backend, worker, beat, frontend ve proxy `up -d`.
9. 180 sn'ye kadar sağlık beklenir. Sonra:
   - `GET /api/v1/health/ready` 200 döner ve `"database": "ok"` der.
   - `GET /api/v1/version` hedef sürümü bildirir.
   - `doctor` `db_connect` ve `migration_head` PASS verir; canlı `alembic_version` imajın
     `migration_head` değerine eşittir.
10. Sonuç durum dosyasına yazılır (`outcome=success`). 9. adım geçmeden başlangıç tamamlanmış
    sayılmaz.

Redis, yazıcılar durdurulduktan sonra ve yeni worker'lar başlamadan önce boşaltılır
(`redis-cli FLUSHALL`). Redis sistem kaydı değildir; broker ve sonuç deposudur. Eski kodun sıraya
koyduğu görevin yeni kodla çalışması sessiz bir uyumsuzluk olurdu. Önemli iş Postgres'tedir ve bir
sonraki beat tick'inde alınır. Toplama filigranlıdır ve başarısız bir çalışma filigranı ilerletmez.

### Otomatik geri alma

Yazıcılar durdurulduktan sonra şunlardan biri olursa tetiklenir:

- `migrate` sıfırdan farklı çıkar (`migration failed; starting automatic rollback`)
- gerekli bir servis 180 sn içinde sağlıklı olmaz (`a required service was not healthy within
  180 seconds; starting automatic rollback`)
- `/api/v1/version` hedef sürüm değildir (`version endpoint did not report <hedef>; starting
  automatic rollback`)
- `doctor` `db_connect` ya da `migration_head` FAIL verir

QRadar'a ulaşılamaması, bir LDAP hatası, geç kalmış bir arama ailesi ya da disk WARN'ı geri almayı
tetiklemez. Bunlar yükseltmeden önce de doğru olabilir; eski bir veritabanını sağlam bir yenisinin
üzerine geri yüklememelidir.

Eylem, az önce alınan yedekle ve `previous_images` ile `rollback`'tir. Bu geri almanın kendi sağlık
kontrolü de başarısız olursa betik yazıcıları yeniden durdurur, yedek yolunu yazdırır, durumu
`outcome=failed` olarak kaydeder ve sıfırdan farklı kodla çıkar
(`Rollback failed; the writers and the UI are stopped. Verified backup: <yol>`). Üçüncü bir
strateji denemez; operatör `./qradar-dash rollback` komutunu yeniden çalıştırır ya da
`./qradar-dash restore <yol>` kullanır.

Geri alma başarılı olsa bile `upgrade` **sıfırdan farklı** kodla çıkar: istenen sürüm çalışmıyor.
Son satır `upgrade to <hedef> failed and was rolled back to <eski>; the broken database was kept`
olur. Bu, otomatik geri almayı OPS-R44 girişiyle bulmanı sağlar.

Geri alma bitince checkout'u artık çalışan sürüme döndür (`git checkout v<önceki>`); böylece
`install` ve compose dosyaları o sürümle eşleşir. Aksi halde bir sonraki `install` yukarıdaki
`install refused` ile durur.

### Kesinti

Yazıcılar `pg_dump` başlangıcından `/api/v1/health/ready` 200 dönene kadar kapalıdır. Çekme bu
pencerenin dışındadır. Baskın süre dökümdür. Yaklaşık 5 GiB altındaki veritabanı için bütçe 10
dakikadır; veritabanı büyükse döküm süresi eklenir.

Bir test VM'de (küçük bir veritabanı) yükseltme kesintisi yaklaşık 15 sn, geri alma yaklaşık 13 sn
ölçüldü. Bunlar bir söz değildir; kendi veritabanın büyükse döküm süresi eklenir.

### Durum dosyasını okuma

`.qradar-dash/state.json`, izin `0600`, git tarafından yok sayılır. Sır, token ya da `.env` değeri
içermez.

```json
{
  "installed_version": "0.1.0",
  "images": {"backend": "sha256:…", "frontend": "sha256:…", "postgres": "sha256:…", "proxy": "sha256:…", "redis": "sha256:…", "qradar-mcp": "sha256:…"},
  "previous_images": null,
  "last_upgrade": {
    "at": "2026-10-30T12:00:00Z",
    "from": "0.1.0",
    "to": "0.2.0",
    "outcome": "success",
    "backup": "/var/backups/qradar-dash/qradar-dash-20261030T115500Z-pre-upgrade"
  }
}
```

`backup` alanı, OPS-5'in yazdığı gibi bir yedek **dizininin** yoludur (`qradar-dash-<utc>-<reason>/`),
tek bir `.dump` dosyası değil. `outcome` değeri `success`, `rolled_back` ya da `failed` olur.
Başarılı bir `restore` aynı bloğa `restored_from` alanını ekler. `status` son girdiyi yazdırır.
`previous_images` `null` ise `rollback` reddeder.

---

## Yedek ve geri alma

> **Alembic downgrade hiçbir zaman kullanılmaz.** Downgrade, o revizyonun sütunlarını ve tablolarını
> verileriyle birlikte siler. Geri alma bir yedeğin geri yüklenmesidir ve geri yüklenen döküm zaten
> eski revizyondadır. Veritabanı imajdan **yeni** ise downgrade etme; imaja uyan yedeği geri yükle.

### Yedek nedir

Bir yedek, yedek kökünün içinde `qradar-dash-<utc>-<reason>/` adlı, izni `0700` olan bir dizindir:

| Dosya | İçerik | İzin |
|---|---|---|
| `database.dump` | `POSTGRES_DB` için `pg_dump -Fc` | `0600` |
| `env.bak` | `.env` kopyası | `0600` |
| `secrets.tar` | QRadar sırları volume'unun ve vekil sertifika volume'unun tar'ı | `0600` |
| `state.json` | varsa o andaki durum dosyası | `0600` |
| `MANIFEST` | sürüm, imaj özetleri, `alembic_version`, veritabanı boyutu, ana makine adı, durum dosyası var mı, sır volume kümesi | `0600` |

`reason` değeri `manual`, `scheduled` ya da `pre-upgrade` olur.

Döküm şifreli metni (örnek token'ları), bcrypt özetlerini ve kayıtlıysa LDAP CA PEM'ini içerir.
`ENCRYPTION_KEY` içermez. `env.bak` olmadan dökümün şifreli verisi okunamaz; `env.bak` anahtarın
kendisidir. İzni `0600` kalır ve destek paketine **kopyalanmaz**. `secrets.tar` token dosyasını ve
TLS özel anahtarını taşır. Redis yedeğe girmez.

### Yedek alma

```bash
./qradar-dash backup [manual|scheduled|pre-upgrade] [--no-verify]
```

`reason` verilmezse `manual` olur. Betik önce diski okur, dökümü konteynerin içinde alır, dışarı
kopyalar ve `.<ad>.partial` altında hazırlar; doğrulama geçince dizini son adına taşır. Her başarı
ya da hata yolu yarım dizini, konteyner içindeki dökümü ve doğrulama veritabanını temizler.

`pg_dump` TimescaleDB için `circular foreign-key constraints` uyarıları yazabilir (`continuous_agg`).
Bunlar beklenir ve yedeğin dökümünü bozmaz; belirleyici olan yedeğin çıkış kodudur ve sağlıklı bir
yedek 0 ile çıkar. Uyarıyı hata sanıp `--no-verify` ekleme.

`backup`, `--no-verify` verilmedikçe veritabanını aynı sunucuda `qradar_obs_restore_test` içine geri
yükler, `alembic_version` değerinin kaynakla eşleştiğini ve `alembic_version` ile `role` üzerinde
`SELECT count(*)` çalıştığını doğrular, sonra geçici veritabanını siler. `pre-upgrade` yedekleri her
zaman doğrulanır; `pre-upgrade` ile `--no-verify` birlikte verilemez (`pre-upgrade backups must be
verified`). Başarısız doğrulama eksik dizini siler ve sıfırdan farklı kodla çıkar.

İki seçenek yedek almaz; ikisi de yalnız zamanlayıcı içindir ve bir nedenle ya da `--no-verify` ile
birleştirilemez:

- `./qradar-dash backup --print-timer`: günlük systemd servisini ve zamanlayıcısını yazdırır,
  hiçbir şey yazmaz.
- `./qradar-dash backup --install-timer`: bunları yazıp etkinleştirir. `root` için
  `/etc/systemd/system` altına, diğer kullanıcılar için o kullanıcının systemd dizinine. Kullanıcı
  zamanlayıcısı için betik "A user timer runs only while that user has a session unless lingering
  is enabled." satırını ve `loginctl enable-linger <kullanıcı>` komutunu yazdırır; komutu çalıştırmaz.

**Disk koruması:** betik yazmadan önce `pg_database_size` ve yedek dosya sisteminin `df` değerini
okur. Boş alan `2 × veritabanı boyutu + 2 GiB` değerinden azsa reddeder. `doctor` `disk_backup` bu
korumayı kullanır.

**Saklama:**

- Son 7 `manual` ya da `scheduled` yedek tutulur.
- Her `pre-upgrade` yedek, iki yeni yükseltme `outcome=success` olana kadar tutulur.
- `last_upgrade.backup` içinde kayıtlı yedek, sonuç `failed` ya da `rolled_back` iken asla silinmez.

Bir yedek, ancak **sonraki** yedeğin koruması o yedek olmadan zaten geçiyorsa silinir. Koruma
geçmezse betik boyutu ve yolu yazdırır ve durur.

Günlük yedek `qradar-dash-backup.timer` adlı systemd zamanlayıcısıyla `./qradar-dash backup
scheduled` çalıştırır; zamanlayıcı kalıcıdır ve 15 dakikalık rastgele bir gecikmesi vardır.

### Geri alma (`rollback`)

```bash
./qradar-dash rollback [--non-interactive --yes]
```

Argüman olarak yalnız bu iki seçeneği alır. `previous_images` ve `last_upgrade.backup` kullanır;
başka bir yedeği seçemez. Sıradan önce, hiçbir konteyner durdurulmadan reddeder:

- `.qradar-dash/state.json` geçersizse
  (`rollback refused: .qradar-dash/state.json field <alan> is invalid or missing`).
- `previous_images` null ise (`rollback refused: previous_images is null`).
- `last_upgrade` yoksa (`rollback refused: last_upgrade is missing`).
- Yedek dizini eksik ya da izinleri güvensizse
  (`rollback refused: last_upgrade.backup is missing or incomplete: <yol>`).
- Yedeğin `MANIFEST` sürümü `last_upgrade.from` ile eşleşmiyorsa
  (`rollback refused: backup version does not match last_upgrade.from`).
- Önceki imajlardan biri yerelde yoksa ya da özeti tutmuyorsa
  (`rollback refused: a previous image is absent or has the wrong local digest`).
- Yedeğin `ENCRYPTION_KEY` değeri ne geçerli anahtar ne de `ENCRYPTION_KEYS_JSON` içindeyse
  (`rollback refused: the backup's ENCRYPTION_KEY is not the current key or in
  ENCRYPTION_KEYS_JSON`).
- Docker kökündeki disk koruması geçmezse.

Sonra, durdurmadan önce onay ister. Etkileşimli modda
`Rollback replaces the live database with <yol>; data written since is kept only in the broken
copy.` yazıp `Type rollback to continue: ` ile stdin'den `rollback` sözcüğünü bekler; yanlış sözcük
`rollback confirmation was not given` ile reddedilir. `--non-interactive` verilmişse `--yes` de
gereklidir (`--non-interactive rollback requires --yes`).

1. Yazıcıları ve arayüzü durdurur.
2. O dökümü uygulama veritabanının üzerine geri yükler: geçici `qradar_obs_restore_<utc>`
   veritabanına geri yükler, `alembic_version` doğrular, bağlantıları sonlandırır, mevcut
   veritabanını `qradar_obs_broken_<utc>` olarak yeniden adlandırır, geri yükleneni `POSTGRES_DB`
   yapar. Bozuk kopya, operatör silene kadar durur; kötü bir geri alma, değiştirdiği veritabanını
   yine bulur. Swap başarılı olduğunda betik bozuk kopyayı ve onu sonra düşürmek için tam komutu
   yazdırır.
3. Önceki imaj özetlerini başlatır; özetleri önceki sürümün etiketlerine çevirir. İmaj çekmez;
   özetler yereldir.
4. Yükseltmedeki aynı sağlık kontrolleri, bu kez **önceki** sürümü bekleyerek.
5. `outcome=rolled_back` kaydeder ve `previous_images` değerini temizler.

Geri alma kendi sağlık kontrolünde başarısız olursa (OPS-R45): yazıcılar yeniden durdurulur, yedek
yolu yazdırılır, durum `outcome=failed` olarak kaydedilir ve üçüncü bir yol denenmez.

`rollback` `alembic downgrade` çalıştırmaz.

Başarılı bir geri almadan sonra (elle ya da otomatik) checkout'u artık çalışan sürüme döndür:
`git checkout v<önceki>`. `install` ve compose dosyaları böylece çalışan sürümle eşleşir.

### Seçili yedeği geri yükleme (`restore`)

```bash
./qradar-dash restore <backup-directory> [--non-interactive --yes]
```

`restore <backup-directory>`, `last_upgrade.backup` olmayan seçili bir yedek için aynı sıradır.
Durdurmadan önce şunları reddeder:

- Dizin eksik ya da dosyaları/izinleri güvensizse
  (`restore refused: the backup directory is incomplete or has unsafe modes`). Dört zorunlu dosya
  (`database.dump`, `env.bak`, `secrets.tar`, `MANIFEST`) ve `MANIFEST` `STATE_JSON=included` diyorsa
  `state.json` gerekir.
- Yedeğin ya da çalışan imajın sürümü okunamıyorsa
  (`restore refused: backup or current image version is unavailable`).
- `MANIFEST` sürümü çalışan sürümden **farklıysa, iki yönde de**: yeni ise
  `restore refused: backup version is newer; upgrade to that version first`; eski ise
  `restore refused: backup version is older; use rollback, or put that version's images back and
  then restore`. Eski koda karşı yeni döküm, migrasyon kuralının önlemek için var olduğu hatadır.
- Yedekteki `ENCRYPTION_KEY` ne geçerli anahtar ne de `ENCRYPTION_KEYS_JSON` içindeyse
  (`restore refused: the backup encryption key is not in the current keyring.`). İki anahtar da
  yazdırılmaz; betik eski anahtarı yerine koymayı söyler.
- Docker kökündeki disk koruması geçmezse
  (`2 × döküm boyutu + 2 GiB`, kopyalanan döküm ve geri yüklenen veritabanı için).

Sonra onay ister: etkileşimli modda stdin'den `restore` sözcüğünü
(`Type restore to replace the application database: `); `--non-interactive` verilmişse `--yes`
ister (`--non-interactive restore requires --yes`).

Swap, mevcut veritabanını `qradar_obs_broken_<utc>` olarak saklar ve bozuk kopyayı düşürmek için
tam komutu hemen yazdırır. Başarılı geri yükleme `Restore complete (manual).` yazar ve şu satırla
biter: `Secrets were not restored.` Yani `secrets.tar` **otomatik geri yüklenmez**; içeriğini
`tar -tf <yedek>/secrets.tar` ile incele ve gerekirse elle uygula. `state.json`'a `restored_from`
alanı işlenir.

---

## Tanı

### `doctor`

```bash
./qradar-dash doctor
```

`doctor` salt okunurdur. İki iş yapar:

1. Konteynerin göremediği ana makine kontrolleri: Docker kökü ve yedek dizininin `df` değeri,
   `timedatectl`, `getenforce`, `docker compose ps`.
2. `docker compose exec -T backend python -m app.cli.doctor`, konteyner içi kontrolleri yazdırır.

Backend konteyneri çalışmıyorsa konteyner içi kısım yerine `doctor_incontainer` FAIL satırı yazılır
(`runbook: OPS-R36`). Çalışıyorsa konteynerin çıktısı değiştirilmeden yazdırılır.

Her satır şöyledir:

```
PASS  db_connect     database accepted SELECT 1
WARN  cadence_late   WAF_COLLECTION last run 2 h 10 m past its cadence    runbook: OPS-R30
FAIL  migration_head database at 0024, image expects 0025                 runbook: OPS-R11
```

Yalnız `PASS`, `WARN`, `FAIL` vardır. Neden tek satırdır. Kılavuz kimliği `OPS-R` ile giriş
numarasıdır (`OPS-R30` 30. girişi gösterir). Çıktıda token, `.env` değeri, DSN ya da
`ENCRYPTION_KEY` yoktur. Bağlantı hataları ana makineyi ve istisna sınıfını söyler, parolayı değil.

Çıkış kodu: tümü PASS ise 0; FAIL olmadan en az bir WARN varsa 1; en az bir FAIL varsa 2.

`doctor` hiçbir yazma işlemi yapmaz. Tek QRadar çağrısı `GET /system/about` isteğidir. QRadar'a ayrı
bir TLS el sıkışması CA paketiyle yapılır ve `SEC` başlığı göndermez; sertifika süresi token
gösterilmeden okunur. `doctor` senkronizasyon başlatmaz, Ariel araması çalıştırmaz ve LDAP'a
bağlanmaz.

**Ana makine kontrolleri**

| Kimlik | Ne yapar | OPS-R |
|---|---|---|
| `disk_docker` | Docker kökünün `df -P` değeri. %70 ve üstü WARN, %90 ve üstü FAIL. | OPS-R14 |
| `disk_backup` | `QRADAR_DASH_BACKUP_DIR` için aynı eşikler ve disk koruması. Veritabanı boyutu okunamazsa uyarır. | OPS-R14 |
| `qradar_clock` | `timedatectl` NTP eşitliği. Eşitsiz saat ya da `timedatectl` yoksa WARN. | OPS-R15 |
| `selinux` | `getenforce` sonucu PASS olarak yazılır, `Enforcing` dahil. Komut yoksa "SELinux is not installed". | yok |
| `containers` | Compose'un döndürdüğü her servis çalışıyor ve sağlıklı mı. Kapalı `mcp` profilindeki `qradar-mcp` ve 0 koduyla çıkmış `migrate` yok sayılır. | OPS-R36, OPS-R41, OPS-R9, OPS-R11 |
| `doctor_incontainer` | Yalnız backend çalışmıyorsa yazılır. | OPS-R36 |

**Konteyner içi kontroller**

| Kimlik | Ne yapar | OPS-R |
|---|---|---|
| `db_connect` | `SELECT 1`. Hazırlık kontrolüyle aynı olgu. | OPS-R36 |
| `migration_head` | `alembic_version` tablosu imajın başıyla aynı mı. | OPS-R11 |
| `timescale` | Eklenti yüklü ve `log_source_metric` bir hypertable mı. | OPS-R14 |
| `retention_policy` | `RETENTION_ENABLED` true iken `log_source_metric` için saklama politikası var mı. | OPS-R14 |
| `redis` | Broker URL'sinde `PING`. | OPS-R9 |
| `celery_workers` | Backend konteynerinin içinden `inspect ping`. En az bir yanıt gerekir. | OPS-R41 |
| `celery_beat` | Redis'teki kalp atışı anahtarı; yaşı 180 sn altında olmalı. | OPS-R9 |
| `qradar_tls` | Yapılandırılmış CA ile el sıkışma. Sertifika bitişi 30 günden yakınsa ya da `QRADAR_TLS_ALLOW_MISSING_AKI` açıksa WARN. | OPS-R2, OPS-R3, OPS-R4, OPS-R18 |
| `qradar_auth` | `GET /system/about`. | OPS-R1 |
| `watermarks` | Her `collection_watermark` satırı: `consecutive_failures` 0 ve gecikme kadansın iki katından az ise PASS. `consecutive_failures` 3 ve üstü ya da `last_error` doluysa FAIL. | OPS-R30 |
| `cadence` | Her `search_cadence` ailesi. Son çalışma yaşı kadans + yerleşme + 60 sn içinde olmalı. | OPS-R30 |
| `families_off` | Varsayılanda kapalı dört bayrak. Yeni kurulum kırmızı görünmesin diye PASS olarak, bayrak adıyla yazılır. | OPS-R7 |
| `errors_recent` | `consecutive_failures > 0` olan filigran satırı sayısı: sıfır PASS, bir ya da iki WARN, üç ve üstü FAIL. | OPS-R30 |
| `pg_database_size` | Veritabanı boyutu. Boş alan ana makine kontrolündedir. | OPS-R14 |
| `encryption` | Saklı bir örnek token'ı çözmeyi dener. Şifreli metin yazdırılmaz. | OPS-R16 |
| `secrets_files` | Token ve CA dosyaları var, boş değil ve uid 10001 tarafından okunabilir. | OPS-R1 |
| `public_origin` | Çalışan ayarlardaki köken. `https` olmalı. | OPS-R12 |
| `version_match` | Frontend probu ile `/api/v1/version` aynı mı. | OPS-R11 |

`families_off` WARN değil PASS verir: yeni kurulumda WAF, IPS, kural eşleşmeleri ve özel kurallar
bilerek kapalıdır. Satır, bir ailenin neden filigranı olmadığını bayrak adıyla gösterir. Arayüz hiç
çalışmamış bir aile için "zamanında" değil, tire gösterir.

`celery_beat` kalp atışına bakar, çünkü beat'in Compose sağlık kontrolü yalnız
`/tmp/celerybeat-schedule` dosyasının var olduğunu sınar. Takılmış ama pid 1'i çıkmamış bir beat
"sağlıklı" kalır.

### `status`

```bash
./qradar-dash status
```

Compose sağlığını (`docker compose ps`), API hazırlık satırını
(`API ready: HTTP <kod> (http://127.0.0.1:<port>/api/v1/health/ready)`), `Installed version`
değerini, her imaj için `Image <servis>: <özet>` satırını ve son yükseltmeyi yazdırır:
`Last upgrade: <outcome>, <from> -> <to> at <at>` ve `Last upgrade backup: <yol> (age <yaş>)`.
Son yükseltme yoksa `Last upgrade: none` yazar. `.env` dosyasını yazdırmaz. `.qradar-dash/state.json`
eksik ya da geçersizse geçersiz alanı adıyla bildirir, `Installed version: unknown` yazar ve 1 ile
çıkar.

### `logs`

```bash
./qradar-dash logs
```

`logs [service]`, `docker compose logs --tail=200 -f` komutudur. Servis verilmezse backend, worker
ve beat izlenir. Uygulamanın günlük filtresi UUID biçimli token'ları yalnız zaten bir sır sözcüğü
geçen satırlarda gizler. `logs` ikinci bir temizleyici değildir; temizleyici destek paketidir.

### `support-bundle`

```bash
./qradar-dash support-bundle
```

`<backup-root>/support-<utc>.tar.gz` dosyasını (izin `0600`) yazar ve yolu yazdırır. Parçalar yedek
kökü altında `0700` bir hazırlık dizininde toplanır; bu dizin ve çıkış/sinyal tuzaklarıyla
temizlenir. Okunamayan her tanı parçası tek satırlık bir `<parça>.error.txt` olur ve paketi
engellemez. Birleştirilen dosyalar, filtrelenmemiş `.env` ve okunabilen sır dosyaları tek bir tar
akışı olarak `docker compose run --rm --no-deps -T backend python -m app.cli.support_bundle`
komutuna stdin'den verilir; temizlenmiş arşiv ancak temizleyici başarılı olursa son adına taşınır.
Backend imajı yoksa ya da temizleyici hata verirse son arşiv oluşmaz.

İçeriği: `doctor.txt` (birleşik rapor), `version.txt`, `compose-ps.txt`, `watermarks.txt` ve
`cadence.txt`, `config.env` (sır satırları **maskelenmez, çıkarılır**) ve `logs/` (her servis için
`docker compose logs --tail=2000 --no-color`, temizlendikten sonra).

İçermedikleri: `.secrets/`, iki sır volume'u, `database.dump`, `env.bak`, `secrets.tar`, GHCR
token'ı, oturum çerezleri, parola özetleri.

Paket oluşturulurken `config.env` dosyasından `SECRET_KEY`, `ENCRYPTION_KEY`,
`ENCRYPTION_KEYS_JSON`, `POSTGRES_PASSWORD`, `QRADAR_SEC_TOKEN`, `OIDC_CLIENT_SECRET`, `LLM_API_KEY`,
`SMTP_PASSWORD` ve adında `WEBHOOK`, `PASSWORD` ya da `TOKEN` geçen her anahtar satırı çıkarılır.
Sırlar destek paketine girmez. Paketi bir bilete eklemeden önce yine de elle gözden geçir.

### Günlük bakış

Bu sırayla:

1. `./qradar-dash status`: sürümler, sağlık, son yedeğin yaşı.
2. `./qradar-dash doctor`: FAIL olan her şey aynı gün ele alınır. WARN okunur.
3. Zamanlanmış aramalar (Scheduled searches) widget'ı: her aileyi gösteren tek ekrandır.
4. Boş alan. `doctor` %90'da FAIL verir. Günlük bakış, retention hâlâ yanlışken bu çizgiye doğru
   yükselişi fark etmek içindir.
5. Sertifika tarihleri. `qradar_tls` 30 gün içinde WARN verir. Dizin testi LDAP sertifikasının
   `not_after` tarihini gösterir.

Üst çubuktaki üç sinyal (backend hazırlığı, son envanter eşitlemesinden QRadar örneği durumu, son
`PHASE_A_VOLUME` çalışması), Scheduled searches widget'ı, widget bayatlığı kuralı (`asOf` kadansın
iki katından eskiyse ya da kadans bilinmiyorsa 4 saatten eskiyse) ve offense toplama uyarısı
`doctor` olmadan görebildiklerindir. Toplama sinyali tek bir ailedir; üst çubuk bir WAF ailesinin
gecikmesini göstermez. `UNKNOWN` yeşil değildir.

Ürün, operatörü kendiliğinden çağırmaz; yalnız zaten ürettiği uyarılar (offense bayatlığı,
yapılandırılan sayıdan sonra arama hataları, davranışsal uyarılar) yapılandırılan bildirim
kanallarına düşer. Boş webhook ayarı kanalın kapalı olduğu anlamına gelir. `doctor` kanala bağlı
olmayan kontroldür. Bu sürümde Prometheus dışa aktarıcısı yoktur.

---

## Belirtiler

Her giriş dört bölümden oluşur: **Belirti**, **Olası neden**, **Doğrulama**, **Çözüm**. Doğrulama
komutu, aksi belirtilmedikçe `./qradar-dash doctor` komutudur. `doctor` satırındaki
`runbook: OPS-R30` yazısını bulmak için `OPS-R30` ara.

### OPS-R1. QRadar kimlik bilgilerini reddediyor

**Belirti:** QRadar kimlik bilgilerini reddediyor. `qradar_auth` FAIL verir.

**Olası neden:** Token yanlış ya da boş; ya da sonunda dosya okuyucunun kırptığı bir satır sonu
karakteri var; ya da token'ın rolü bu uç noktayı çağıramıyor. 401 ve 403 tek bir cümleyle bildirilir.

**Doğrulama:** `qradar_auth` FAIL, bu cümleyle.

```bash
./qradar-dash doctor
```

**Çözüm:** `.secrets/qradar.sec` dosyasını değiştir, `load-secrets.sh` komutunu yeniden çalıştır,
sonra konteynerin içinde `qradar add` komutunu yeniden çalıştır (Kurulum, adım 8):

```bash
./deploy/load-secrets.sh
```

```bash
docker compose run --rm backend \
  python -m app.cli.qradar add \
    --name <name> \
    --url <https-url> \
    --token-file /run/secrets/qradar_sec_token \
    --ca-file /run/secrets/qradar_ca.pem \
    --api-version <version>
```

Dosya ve örnek satırı iki ayrı kopyadır. Yalnız dosyayı değiştirmek toplamayı eski şifreli metinle
bırakır.

### OPS-R2. `unable to get issuer certificate`

**Belirti:** `unable to get issuer certificate` hatası. `qradar_tls` FAIL verir.

**Olası neden:** CA dosyasında kök değil, yalnız ara sertifika var. QRadar yalnız yaprak sertifikayı
gönderir.

**Doğrulama:** `qradar_tls` FAIL ve aşağıdaki openssl komutu aynı şekilde başarısız olur.

```bash
./qradar-dash doctor
```

```bash
openssl s_client -connect qradar.your.internal:443 \
  -CAfile .secrets/qradar-ca.pem -verify_return_error </dev/null 2>&1 | grep Verify
```

**Çözüm:** Kökü, sonra ara sertifikayı `.secrets/qradar-ca.pem` dosyasında birleştir. Sonra
`load-secrets.sh` ve `qradar add` komutlarını yeniden çalıştır (OPS-R1'deki komutlar).

### OPS-R3. `Missing Authority Key Identifier`

**Belirti:** `SSLCertVerificationError: Missing Authority Key Identifier`.

**Olası neden:** Uygulama `VERIFY_X509_STRICT` ayarını her yorumlayıcıda kendisi açar; imajın Python
3.12 sürümü dahil. QRadar sertifikasında SKI var, AKI yok.

**Doğrulama:** İstisna metni görülür, buna karşın aynı CA ile `openssl s_client` başarılı olur.

```bash
./qradar-dash doctor
```

```bash
openssl s_client -connect qradar.your.internal:443 \
  -CAfile .secrets/qradar-ca.pem -verify_return_error </dev/null 2>&1 | grep Verify
```

**Çözüm:** Konsol sertifikasını yeniden düzenlet. O zamana kadar `QRADAR_TLS_ALLOW_MISSING_AKI=true`
kullan. Bu ayar doğrulamayı kapatmaz: zincir, süre ve ana makine adı doğrulaması sürer. Ayar açıkken
`doctor` WARN verir.

### OPS-R4. Ana makine QRadar'a ulaşıyor, konteyner ulaşamıyor

**Belirti:** Ana makineden QRadar'a erişim var, konteynerden yok. `qradar_tls` FAIL verir ve hata
bir ağ sınıfındandır. Sağlayıcı taşıma hatalarını `Could not reach QRadar` olarak bildirir.

**Olası neden:** libvirt köprüsü iletilen trafiği reddediyor.

**Doğrulama:** Ana makineden yukarıdaki openssl komutu çalışır. `docker compose exec backend`
içinden aynı ana makineye TCP bağlantısı başarısız olur.

```bash
./qradar-dash doctor
```

**Çözüm:** README'deki macvlan geçersiz kılma (override) bölümü: `./deploy/create-vmnet.sh` ve
`backend`, `celery-worker`, `celery-beat` ile `migrate` servislerini o ağa koyan,
git tarafından yok sayılan bir `docker-compose.override.yml`. Ya da bir rota. Betik bir güvenlik duvarı
değişikliği yapmaz.

```bash
./deploy/create-vmnet.sh
```

### OPS-R5. `QRadar Ariel metric search timed out`

**Belirti:** Worker günlüğünde `QRadar Ariel metric search timed out` ve filigranın `last_error`
alanında aynı ifade.

**Olası neden:** Metrik araması iki dakikalık sınır içinde bitmedi.

**Doğrulama:** İfade worker günlüğünde ve filigranın `last_error` alanında.

```bash
./qradar-dash doctor
```

```bash
./qradar-dash logs
```

**Çözüm:** Kadansı ya da kaynak kümesini daralt. Bu kılavuz, ilk adım olarak sınırı yükseltmeni
söylemez. Takılmış bir konsol bir worker'ı kilitlememelidir.

### OPS-R6. `QRadar Ariel metric search was truncated at N rows`

**Belirti:** `last_error` alanında `QRadar Ariel metric search was truncated at N rows`.

**Olası neden:** Sonuç sayfa tavanına çarptı ve toplayıcı eksik kaynakları sıfırla doldurmayı
reddediyor. WAF ve IPS de sınırlanmış bir dökümü aynı şekilde işaretler. Kesilmiş döküm üründe
`TRUNCATED` olur, sessiz bir sıfır olmaz.

**Doğrulama:** Bu ifade `last_error` alanında.

```bash
./qradar-dash doctor
```

**Çözüm:** Arama bölünmeli ya da kaynak kümesi küçültülmelidir.

### OPS-R7. Bir aile veri göstermiyor ve arayüz "toplanmadı" diyor

**Belirti:** Bir ailede veri yok ve arayüz toplanmadığını söylüyor.

**Olası neden:** Aile bayrağı kapalı, profil devre dışı ya da izleme kapalı. WAF için:
`WAF collection is disabled (WAF_COLLECTION_ENABLED=false)` ve üç komşu mesajı. IPS için:
`IPS_COLLECTION_ENABLED` varsayılanda false'tur ve toplayıcı `profile disabled` ya da
`monitoring disabled` döndürür.

**Doğrulama:** `families_off` satırı ya da filigranın `last_error` alanı.

```bash
./qradar-dash doctor
```

**Çözüm:** Bayrağı yalnız profil geçerli olduktan sonra aç. Yeni kurulumda kapalı bir aile beklenen
durumdur (Kurulum, Sihirbaz).

### OPS-R8. WAF profili bir düzenlemeden sonra toplamıyor

**Belirti:** Düzenlemeden sonra WAF profili toplamıyor. IPS için de aynı biçim vardır:
`current profile version is not validated`.

**Olası neden:** Kaydedilen sürüm doğrulanmamış.

**Doğrulama:** Bu cümle worker günlüğünde ya da kaynağın yönetim sayfasında görünür.

```bash
./qradar-dash logs
```

**Çözüm:** `/admin/waf-sources` ya da `/admin/ips-sources` sayfasında doğrulamayı çalıştır ve durum
geçerli olana kadar bekle. Doğrulanmamış eşleme üzerinden toplama yapma.

### OPS-R9. Beat "sağlıklı" görünüyor ve hiçbir şey zamanlanmıyor

**Belirti:** Beat sağlıklı görünüyor ama hiçbir görev zamanlanmıyor.

**Olası neden:** Compose kontrolü yalnız zamanlama dosyasına bakar. Beat artık başlangıçta kalp
atışını yazar; bu yüzden bir yeniden başlatmadan ya da bir yükseltmeden (Redis `FLUSHALL`) sonraki
ilk iki dakikadaki bir `celery_beat` FAIL geçicidir, gerçek bir arıza değildir.

**Doğrulama:** `celery_beat` FAIL **iki dakikadan uzun sürüyor** ve etkin her aile için `cadence`
WARN. Yeniden başlatmadan ya da yükseltmeden sonra ilk iki dakikada henüz karar verme.

```bash
./qradar-dash doctor
```

**Çözüm:**

```bash
docker compose restart celery-beat
```

Yeniden başlatmadan iki dakika sonra `celery_beat` hâlâ FAIL veriyorsa bu gerçek bir belirtidir:
worker günlüklerine ve OPS-R41'e bak.

### OPS-R10. `Offense collection is stale for <name>`

**Belirti:** `Offense collection is stale for <name>` uyarısı.

**Olası neden:** Offense filigranı `offense_stale_after_seconds` değerinden (varsayılan 900) eski
ya da `consecutive_failures` sıfır değil.

**Doğrulama:** Uyarı başlığı ve filigran satırı.

```bash
./qradar-dash doctor
```

**Çözüm:** Offense toplayıcısının worker günlüklerine, sonra QRadar'a erişilebilirliğe bak
(OPS-R1 ... OPS-R4). Atlanan bir kilit bu hata değildir (bkz. OPS-R29).

```bash
./qradar-dash logs
```

### OPS-R11. Uygulama ayakta ama yazma işlemleri başarısız; ya da `doctor` başın eşleşmediğini söylüyor

**Belirti:** Uygulama açık, yazmalar başarısız. Ya da `doctor` veritabanı başının eşleşmediğini
söylüyor.

**Olası neden:** Migrate çalışmadı ya da migrasyon sırasında eski worker'lar hâlâ ayaktaydı.

**Doğrulama:** `migration_head` FAIL.

```bash
./qradar-dash doctor
```

**Çözüm:** Yazıcıları durdur, `VERSION` ile eşleşen imajda `run --rm migrate` çalıştır, yazıcıları
başlat:

```bash
docker compose stop backend celery-worker celery-beat
docker compose run --rm migrate            # alembic upgrade head
docker compose up -d backend frontend celery-worker celery-beat
```

Veritabanı imajdan **yeniyse downgrade etme**. İmaja uyan yedeği geri yükle (Yedek ve geri alma).

```bash
./qradar-dash restore <backup-directory>
```

### OPS-R12. Arayüz açılıyor ama her API çağrısı başarısız

**Belirti:** Arayüz yükleniyor, her API çağrısı başarısız.

**Olası neden:** Tarayıcı başka bir kökene çağrı yapıyor. İmaj `NEXT_PUBLIC_API_BASE_URL` olmadan
derlenmiş ve localhost'a düşmüştür; ya da `PUBLIC_ORIGIN` adres çubuğundaki URL ile eşleşmiyordur.

**Doğrulama:** Tarayıcının ağ günlüğünde `localhost:8000` ya da eksik bir
`Access-Control-Allow-Origin` görünür. `public_origin` satırı operatörün ayarladığını sandığı kökeni
gösterir.

```bash
./qradar-dash doctor
```

**Çözüm:** `/api/v1` ile derlenmiş bir imajı çek ya da yeniden derle ve `PUBLIC_ORIGIN` değerini
tam `https` köken olarak ayarla.

### OPS-R13. Bir konteyner checkout'taki bir dosyayı okuyamıyor

**Belirti:** Bir konteyner, checkout içinde olan bir dosyayı okuyamıyor.

**Olası neden:** SELinux `user_home_t`. MCP servisi checkout'u bağlar; etkin bir `mcp` profili
bu yüzden zorlayıcı (enforcing) ana makinede başarısız olur.

**Doğrulama:** `ausearch` ya da günlük, `container_t` için `denied` gösterir ve `getenforce`
çıktısı `Enforcing` olur.

```bash
getenforce
```

**Çözüm:** **Yeniden etiketleme yapma.** SELinux kapatılmaz ve `:z` kullanılmaz. `load-secrets.sh`
kullanmaya devam et. Ayarlar bir imaja gömülene kadar `mcp` profilini kapalı bırak.

### OPS-R14. Postgres `No space left on device` veriyor ya da `doctor` diski FAIL gösteriyor

**Belirti:** Postgres `No space left on device` hatası veriyor ya da `doctor` disk satırı FAIL
gösteriyor.

**Olası neden:** `retention_enabled` false idi ya da true idi ama `apply_timescale_policies` görevi
hiç çalışmadı. Docker json günlükleri 20 MB × 5 ile sınırlıdır ve diski dolduran şey bunlar değildir.

**Doğrulama:** `disk_docker` ya da `disk_backup` FAIL ve `pg_database_size` satırı.

```bash
./qradar-dash doctor
```

**Çözüm:** Yer aç, sonra bir saklama politikasının var olduğunu doğrula (`retention_policy` PASS).
Docker günlük dosyalarını silmek dolu bir veri volume'unu düzeltmez.

### OPS-R15. Ana makine askıya alındıktan sonra filigranlar atlıyor ya da çok sayıda kaynak `TIMESTAMP_DELAY` gösteriyor

**Belirti:** Ana makine askıya alındıktan sonra filigranlar atlıyor ya da birçok kaynak
`TIMESTAMP_DELAY` gösteriyor.

**Olası neden:** Ana makine saati sıçradı. Beat UTC kullanır. Gecikme dedektörü gerçek bir anomali
türüdür.

**Doğrulama:** `qradar_clock` WARN ve `timedatectl` eşitli değil.

```bash
timedatectl
```

**Çözüm:** NTP'yi etkinleştir ve sonraki tam kovaları bekle. Filigranları elle düzenleme.

### OPS-R16. `KeyRotationError` / şifre çözme başarısız

**Belirti:** `KeyRotationError` ya da şifre çözme başarısız.

**Olası neden:** `ENCRYPTION_KEY` değişti ve eski anahtar `ENCRYPTION_KEYS_JSON` içinde yok.

**Doğrulama:** `encryption` FAIL.

```bash
./qradar-dash doctor
```

**Çözüm:** Satırları şifreleyen anahtarı anahtarlığa geri koy. Kopya, yedeğin `env.bak` dosyasıdır.
Hatayı "düzeltmek" için yeni anahtar üretme.

### OPS-R17. Backend `Unsafe production configuration` ile yeniden başlıyor

**Belirti:** Backend `Unsafe production configuration` hatasıyla yeniden başlıyor.

**Olası neden:** Başlangıç kontrollerinden biri: `DEBUG`, kapalı TLS, kısa `SECRET_KEY`, eksik
`ENCRYPTION_KEY`, `QRADAR_PROVIDER=mock`, token yok, otonom LLM, hatalı `PUBLIC_ORIGIN` ya da
`AUTH_PROVIDER=oidc`.

**Doğrulama:** İzleme (traceback) maddeleri listeler.

```bash
./qradar-dash logs
```

**Çözüm:** Listelenen maddeyi düzelt. `LAB_MODE` ayrı bir reddetmedir.

### OPS-R18. `QRADAR_HOST must use https://`

**Belirti:** `QRADAR_HOST must use https://`. `doctor` `qradar_tls` için bu durumda
`runbook: OPS-R18` yazar.

**Olası neden:** URL'de `https` şeması yok. Örnek kayıt defteri de aynı kuralı uygular.

**Doğrulama:** İstisna metni.

```bash
./qradar-dash doctor
```

**Çözüm:** URL'yi düzelt. Düz metin (plaintext) ayarı yoktur.

### OPS-R19. `WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS`

**Belirti:** Başlangıçta `WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS` hatası.

**Olası neden:** WAF toplama ve WAF saklama ikisi de açık ve saklama 28 günlük geriye bakıştan uzun
değil.

**Doğrulama:** Başlangıç hatası.

```bash
./qradar-dash logs
```

**Çözüm:** Saklamayı artır ya da geçmişi azalt. Sınırsız WAF büyümesini kabul etmiyorsan, süreci
açmak için saklamayı kapatma.

### OPS-R20. Frontend konteyneri `getaddrinfo EAI_AGAIN` ile çıkıyor

**Belirti:** Frontend konteyneri `getaddrinfo EAI_AGAIN` hatasıyla çıkıyor.

**Olası neden:** `HOSTNAME` konteyner kimliği olarak kalmış (`frontend/Dockerfile` bu yüzden onu
`0.0.0.0` yapar).

**Doğrulama:** Bu hata frontend günlüğünde.

```bash
./qradar-dash logs frontend
```

**Çözüm:** `HOSTNAME=0.0.0.0` değerini koru. Bu satırı kaldıran yeniden derlenmiş bir imaj hatayı
geri getirir.

### OPS-R21. Bir analistin trafiği herkes için API'de 429 döndürüyor

**Belirti:** Bir analistin trafiği herkes için 429 döndürüyor.

**Olası neden:** `FORWARDED_ALLOW_IPS` vekili içermiyor; bu yüzden her istemci köprü adresini
paylaşıyor. Varsayılan sınır dakikada 120 istektir (`RATE_LIMIT_DEFAULT`).

**Doğrulama:** Farklı tarayıcılar için backend günlükleri tek bir istemci adresi gösterir ve
`FORWARDED_ALLOW_IPS` boş ya da `127.0.0.1` olur. Uvicorn erişim günlüğü, bu backend'de varsayılan
olarak kapalıdır; satırı görmek için o günlükleyiciyi yeniden etkinleştir.

```bash
./qradar-dash logs
```

**Çözüm:** `FORWARDED_ALLOW_IPS` değerini vekil adresine ayarla. Paketle gelen vekilde bu
`172.28.10.10` adresidir. Harici vekilde, adresi `docker network inspect <network>` ile ölç. Asla `*`
yazma.

### OPS-R22. Oturum açmış tarayıcı `Cross-site request refused` alıyor

**Belirti:** Oturum açmış bir tarayıcı `Cross-site request refused` alıyor.

**Olası neden:** `Origin`, `PUBLIC_ORIGIN` değil. Tipik durum: sihirbaz `https://soc.example`
sakladı ama operatör `https://192.0.2.10` ya da bir `http` URL'si açtı. Çerez üretimde `Secure`
olduğundan `http` kökeni çerezi de düşürür.

**Doğrulama:** 403 gövdesi bu cümleyi içerir.

```bash
./qradar-dash doctor
```

**Çözüm:** Sertifikadaki ana makine adını kullan.

### OPS-R23. Oturum açma 503 `The directory server is unavailable` döndürüyor

**Belirti:** Oturum açma 503 `The directory server is unavailable` döndürüyor.

**Olası neden:** LDAPS el sıkışması ya da bind tamamlanmadı. `/admin/directory` sayfasındaki Test
düğmesi yapılandırılmış TLS nedenini döndürür. Koddan bilinen yaygın durumlar: CA zincire bağlanmıyor;
sertifikanın süresi dolmuş ya da henüz geçerli değil; ana makine adı eşleşmiyor (`doesn't match`
ifadesi korunur); 636 portu kapalı (bir taşıma hatası); `ldaps://` ile birlikte `start_tls`;
StartTLS olmadan `ldap://`.

**Doğrulama:** Dizin sayfasındaki test sonucu. Tahmini bir paket yakalama değil.

```bash
./qradar-dash doctor
```

**Çözüm:** Testin adını verdiği neden. Kurulum bu değerleri toplamaz.

### OPS-R24. Oturum açma `Invalid username or password` diyor ve dizin sağlam

**Belirti:** Oturum açma `Invalid username or password` diyor ama dizin sağlam.

**Olası neden:** HTTP gövdesi her zaman bu cümledir; dizin kapalıyken de, parola yanlışken de.

**Doğrulama:** Ekrandaki mesaj değil, denetim (audit) nedenine bak: `ldap_disabled` ya da
`invalid_credentials`.

```bash
./qradar-dash doctor
```

**Çözüm:** Dizin açık olmalıysa etkinleştir ve Test düğmesini kullan. Beş başarısız denemede
kullanıcı adı 15 dakika kilitlenir. Kilit, bilinen yerel ya da mevcut bir kullanıcının başarısız
oturum açmasıdır; 503 değildir.

### OPS-R25. Bir dizin kullanıcısı oturum açıyor ve her sayfa yasak

**Belirti:** Bir dizin kullanıcısı oturum açıyor ve her sayfa yasak.

**Olası neden:** Satır yoksa ilk başarılı bind kullanıcıyı oluşturur ve rol atamaz. `User.roles`
boş kalır. Roller AD'den okunmaz (D-19).

**Doğrulama:** `/admin/users` kullanıcıyı rolsüz gösterir ve `/me` başarılı olur.

```bash
./qradar-dash doctor
```

**Çözüm:** Bir yönetici rol atar. Düzeltilecek bir AD grubu yoktur.

### OPS-R26. Son yönetici devre dışı bırakılamıyor

**Belirti:** Son yönetici devre dışı bırakılamıyor.

**Olası neden:** Son etkin `admin:*` sahibi korunur (AUTH-1).

**Doğrulama:** Kullanıcılar API'sinden HTTP 409.

```bash
./qradar-dash doctor
```

**Çözüm:** Önce başka bir yönetici oluştur. O yöneticinin parolası kaybolduysa `create-admin` acil
durum yoludur; parolayı sıfırlar ve oturumları iptal eder:

```bash
docker compose run --rm --no-deps backend \
  python -m app.cli.users create-admin --username <name>
```

### OPS-R27. Arayüz yalnız parola değiştirmeye izin veriyor

**Belirti:** Arayüz yalnız parola değiştirmeye izin veriyor. API'den yapılan her başka istek 403
`A password change is required before any other request.` ile reddediliyor.

**Olası neden:** `must_change_password` ayarlı. `create-admin` bunu her zaman ayarlar.

**Doğrulama:** Hesap menüsü, `/me` ya da 403 gövdesi.

```bash
./qradar-dash doctor
```

**Çözüm:** Parolayı değiştir. Değiştirene kadar yalnız `/me`, parola yolu ve oturum kapatma
çağrılabilir. Bu ilk oturum durumudur, arıza değildir.

### OPS-R28. `pagination stopped at the page ceiling`

**Belirti:** `pagination stopped at the page ceiling`.

**Olası neden:** Bir liste uç noktası `max_pages` sınırına çarptı. Offense ve kural sayfa sınırları
varsayılanda 20'dir.

**Doğrulama:** Bu günlük satırı.

```bash
./qradar-dash logs
```

**Çözüm:** İlgili `*_MAX_PAGES` değerini ancak konsolun sınırdan büyük olduğunu doğruladıktan sonra
artır. O zamana kadar envanter eksiktir. Arayüze eksik satırların sıfır olduğu söylenmemelidir.

### OPS-R29. `offense collection skipped; another worker holds the lock`

**Belirti:** `offense collection skipped; another worker holds the lock`.

**Olası neden:** İkinci bir worker danışma (advisory) kilidini kaybetti.

**Doğrulama:** Bu günlük satırı ve sonraki başarılı bir çalışma.

```bash
./qradar-dash logs
```

**Çözüm:** Sonraki bir çalışma başarılıysa gerekmez. Her çalışma atlanıyorsa iki beat zamanlanmıştır.
`status` tek bir `celery-beat` göstermelidir.

```bash
./qradar-dash status
```

### OPS-R30. Bir arama ailesi geç kalmış

**Belirti:** Bir arama ailesi geç kalmış.

**Olası neden:** Son çalışma, kadans + yerleşme + 60 sn değerinden eski. Yerleşme (settle)
varsayılanda 120 sn'dir.

**Doğrulama:** `cadence` WARN ve Scheduled searches widget'ı. Üst çubuktaki toplama sinyali yalnız
`PHASE_A_VOLUME` ailesidir; geç kalmış bir WAF ailesi o göstergeyi oynatmaz.

```bash
./qradar-dash doctor
```

**Çözüm:** O ailenin worker günlüklerine bak, sonra OPS-R5, OPS-R6, OPS-R7 ve OPS-R9 girişlerine.

```bash
./qradar-dash logs
```

### OPS-R31. `network qradar-vmnet not found`

**Belirti:** `network qradar-vmnet not found`.

**Olası neden:** Docker daemon yeniden başladıktan sonra bayat kalmış bir macvlan.

**Doğrulama:** Compose bu ağı adlandırır ve `docker network ls` listesinde yoktur.

```bash
docker network ls
```

**Çözüm:** README'deki yeniden oluşturma komutu. Bu geçersiz kılmayı hiç oluşturmamış bir üretim
kurulumu bunu görmez.

```bash
docker network rm qradar-vmnet && bash deploy/create-vmnet.sh
```

### OPS-R32. `volume qradar-observability-secrets not found`

**Belirti:** `volume qradar-observability-secrets not found`.

**Olası neden:** Volume `external: true` olduğundan `down -v` onu silemez; henüz oluşturulmamış.

**Doğrulama:**

```bash
docker volume ls
```

**Çözüm:**

```bash
./deploy/load-secrets.sh
```

### OPS-R33. Eski konteynerler hâlâ ayaktayken migrate reddediliyor ya da yeni sütunlar eksik

**Belirti:** Eski konteynerler hâlâ ayaktayken migrate reddediliyor ya da yeni sütunlar eksik.

**Olası neden:** OPS-R11 ile aynı mekanizma.

**Doğrulama:** `run migrate` sırasında `docker compose ps` bir worker'ın ayakta olduğunu gösterir.

```bash
docker compose ps
```

**Çözüm:** `upgrade` gibi önce yazıcıları durdur (OPS-R11'deki komutlar).

### OPS-R34. Biri `alembic downgrade` çalıştırdı ve satırlar gitti

**Belirti:** Biri `alembic downgrade` çalıştırdı ve satırlar kayboldu.

**Olası neden:** Downgrade o revizyonun tablolarını siler.

**Doğrulama:** `migration_head` geriye gitti ve önceki sürümün sahip olduğu bir tablo yok.

```bash
./qradar-dash doctor
```

**Çözüm:** Yükseltme öncesi yedeğin `restore` ile geri yüklenmesi. Geri almayı "bitirmek" için
downgrade çalıştırma.

```bash
./qradar-dash restore <backup-directory>
```

### OPS-R35. `.env` düzenlendikten sonra `password authentication failed for user qradar_obs`

**Belirti:** `.env` düzenlendikten sonra `password authentication failed for user qradar_obs`.

**Olası neden:** `POSTGRES_PASSWORD` veri dizini oluşturulurken uygulanır.

**Doğrulama:** Postgres günlüğü kimlik doğrulamasının başarısız olduğunu söyler ve `postgres_data`
volume'u zaten vardı.

```bash
./qradar-dash logs
```

**Çözüm:** Eski parolayı `env.bak` dosyasından geri koy. Postgres içindeki parolayı değiştirmek,
sonradan ve bilinçli yapılan bir `ALTER ROLE` işidir; ardından `.env` aynı değere düzenlenir. Betik
bunu iki haftalık sürümde yapmaz.

### OPS-R36. `/api/v1/health/live` 200 ama `/ready` değil

**Belirti:** `/api/v1/health/live` 200 dönüyor, `/ready` dönmüyor.

**Olası neden:** Süreç ayakta, veritabanı değil.

**Doğrulama:** O iki URL.

```bash
curl http://127.0.0.1:8000/api/v1/health/live
curl http://127.0.0.1:8000/api/v1/health/ready
```

**Çözüm:** Postgres sağlığına bak, sonra OPS-R14 ya da OPS-R35.

### OPS-R37. Özel kurallar hiç değerlendirilmiyor

**Belirti:** Özel kurallar hiç değerlendirilmiyor.

**Olası neden:** `CUSTOM_RULES_ENABLED` varsayılanda false'tur ve false iken beat görevi hiçbir şey
yapmaz. `RULE_MATCH_COLLECTION_ENABLED` de aynı biçimdedir.

**Doğrulama:** `families_off`.

```bash
./qradar-dash doctor
```

**Çözüm:** Operatör motoru istediğinde ayarı etkinleştir.

### OPS-R38. `ghcr.io` üzerinden `docker pull` denied dönüyor

**Belirti:** `ghcr.io` üzerinden `docker pull` denied dönüyor.

**Olası neden:** Depo özel ve `.secrets/ghcr.token` eksik, yanlış ya da `read:packages` yetkisi yok.

**Doğrulama:** O dosyayla `docker login`, sonra tek bir imaj çek.

```bash
docker login
```

**Çözüm:** Yeni bir token. Kayıt defterine hiç ulaşılamıyorsa yol `--offline` seçeneğidir.

### OPS-R39. Dizin sayfası yapıştırılan CA'yı reddediyor

**Belirti:** Dizin sayfası operatörün yapıştırdığı CA'yı reddediyor.

**Olası neden:** PEM bir özel anahtar içeriyor, sertifika değil ya da süresi dolmuş.

**Doğrulama:** 422 yanıtı her nedeni listeler (AUTH-1).

```bash
./qradar-dash doctor
```

**Çözüm:** Yalnız CA sertifikasını yapıştır.

### OPS-R40. Boş dizin parolası denemeden bile reddediliyor

**Belirti:** Boş dizin parolası hiç denenmeden reddediliyor.

**Olası neden:** Boş parola, soket açılmadan önce reddedilir.

**Doğrulama:** Test sonucu `an empty password is refused` der.

```bash
./qradar-dash doctor
```

**Çözüm:** Parolayı yaz. Bu Test düğmesidir, kayıtlı bir servis hesabı değildir. Servis hesabı
yoktur (AUTH-1).

### OPS-R41. Worker sağlık kontrolü `inspect ping` ile titreşiyor

**Belirti:** `celery-worker` servisinin Compose sağlığı gidip geliyor (flap) ya da `unhealthy`
kalıyor, buna karşın `doctor` `celery_workers` PASS verir.

**Olası neden:** Compose sağlık kontrolü `celery@$HOSTNAME` adresine ping atar
(`docker-compose.yml`, `celery-worker` `healthcheck`). Ana makine adı Celery düğüm adıyla
eşleşmezse filtreli ping boş döner; worker'lar ayakta olsa bile kontrol başarısız olur.

**Doğrulama:** İki görünüm çelişir: `doctor` `celery_workers` satırı en az bir pong bildirir (filtresiz
`inspect ping`), `docker compose ps` ise `celery-worker` için `unhealthy` ya da dalgalanan bir sağlık
gösterir. Bu çelişki adlandırma uyuşmazlığının kanıtıdır; ikisi de down ise sorun worker'ın kendisidir.

```bash
docker compose ps
```

**Çözüm:** Düğüm adı ile sağlık kontrolü aynı adı kullanmalıdır. As built, `doctor` `celery_workers`
tek bir **filtresiz** `inspect ping` yapar: en az bir yanıt PASS olur ve worker sayısını ve düğüm
adlarını yazar; hiç yanıt yoksa FAIL verir (`no Celery worker returned pong`, `runbook: OPS-R41`).
Yani adlandırma uyuşmazlığı artık bütün worker kümesinin sahte FAIL'i değil, Compose sağlığında bir
titreşimdir. Sağlık kontrolündeki adı Celery düğüm adıyla hizala; filtresiz ping bir düğüm
döndürüyorsa worker'lar ayaktadır.

### OPS-R42. Oturum ana makineden bir kez çalışıyor, tarayıcıdan çalışmıyor

**Belirti:** Oturum açma ana makineden bir kez çalışıyor, tarayıcıdan çalışmıyor.

**Olası neden:** Oturum çerezi `Secure` ve `SameSite=Strict`. HTTPS olmayan bir kökendeki tarayıcı
onu saklamaz.

**Doğrulama:** `/api/v1/auth/login` 200 döndürdükten sonra tarayıcıda `qd_session` çerezi görünmez.

```bash
./qradar-dash doctor
```

**Çözüm:** `https` genel kökenini kullan. Loopback HTTP üretim arayüzü değildir. Komut satırından
API ile oturum açıyorsan isteğe siteyle eşleşen bir `Origin` başlığı ekle (`https://<ad>`); CSRF
koruması eksik başlığa 403
`Cross-site request refused: the request has no Origin or Referer header.` döner.

### OPS-R43. `restore` seçilen yedeği reddediyor

**Belirti:** `restore`, seçilen yedeği hiçbir şey durdurmadan reddediyor.

**Olası neden:** Dizin `database.dump`, `env.bak`, `secrets.tar`, `MANIFEST` (ya da
`STATE_JSON=included` ise `state.json`) dosyalarından biri eksik ya da izinleri `0700`/`0600`
değil; `MANIFEST` sürümü çalışan sürümden **iki yönde de** farklı; yedeğin `ENCRYPTION_KEY` değeri
ne geçerli anahtar ne de `ENCRYPTION_KEYS_JSON` içinde; Docker kökündeki disk koruması geçmiyor;
ya da onay sözcüğü yanlış yazıldı.

**Doğrulama:** Reddetme satırı nedeni tam olarak yazar
(`restore refused: …` / `restore confirmation was not given`).

```bash
./qradar-dash restore <backup-directory>
```

**Çözüm:** Satırın adını verdiği neden:

- Dizin eksik/bozuksa yedeği yeniden al (`./qradar-dash backup`).
- `backup version is newer` ise önce `./qradar-dash upgrade <sürüm>` ile o sürüme geç.
- `backup version is older` ise `./qradar-dash rollback` kullan (bkz. OPS-R45) ya da o sürümün
  imajlarını geri koyup `restore`'u yeniden çalıştır.
- Anahtar uyuşmazlığında eski `ENCRYPTION_KEY` değerini `ENCRYPTION_KEYS_JSON` içine koy
  (bkz. OPS-R16); yeni anahtar üretme.
- Disk yetersizse yer aç (bkz. OPS-R14).

### OPS-R44. Bir yükseltme otomatik geri alındı

**Belirti:** `upgrade` sıfırdan farklı kodla çıktı ve son satır
`upgrade to <hedef> failed and was rolled back to <eski>; the broken database was kept` oldu.
Çalışan sürüm yine eski sürümdür.

**Olası neden:** Yazıcılar durdurulduktan sonra tetikleyicilerden biri: `migrate` sıfırdan farklı
çıktı, gerekli bir servis 180 sn içinde sağlıklı olmadı, `/api/v1/version` hedefi bildirmedi, ya da
`doctor` `db_connect`/`migration_head` FAIL verdi. QRadar'a ulaşılamaması, bir LDAP hatası, geç
kalmış bir arama ailesi ya da disk WARN'ı bu geri almayı tetiklemez.

**Doğrulama:** Durum dosyası son yükseltmeyi `outcome=rolled_back` gösterir ve `doctor` sağlıklıdır.

```bash
./qradar-dash status
```

**Çözüm:** Geri alma tamamlandı; veri kaybı yok, yazıcılar eski sürümle çalışıyor. Yukarıdaki
tetikleyicinin nedenini gider (migrate çıktısı, servis sağlığı, sürüm ucu, OPS-R11), sonra
yükseltmeyi yeniden dene. `outcome=rolled_back` sonraki yükseltmeyi engellemez; `outcome=failed`
engeller (bkz. OPS-R45).

### OPS-R45. Geri alma başarısız oldu

**Belirti:** `Rollback failed; the writers and the UI are stopped. Verified backup: <yol>` yazıldı;
yazıcılar durmuş ve durum dosyası son yükseltmeyi `outcome=failed` gösteriyor.

**Olası neden:** Geri yüklenen dökümün swap'i ya da **önceki** sürümün sağlık kontrolü başarısız
oldu. Betik üçüncü bir strateji denemez; kontrollü durur.

**Doğrulama:** `status` `Last upgrade: failed, …` yazar ve `doctor` FAIL verir; yazıcılar kapalıdır.

```bash
./qradar-dash status
```

**Çözüm:** Yazdırılan nedeni gider, sonra `./qradar-dash rollback` komutunu yeniden çalıştır; o da
olmazsa `./qradar-dash restore <yol>` ile aynı doğrulanmış yedeği geri yükle. Bozuk veritabanı
kopyası silinmeden durur.
