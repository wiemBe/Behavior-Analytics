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
2. [Çevrimdışı paket](#çevrimdışı-paket)
3. [Dizin](#dizin)
4. [Yükseltme](#yükseltme)
5. [Yedek ve geri alma](#yedek-ve-geri-alma)
6. [Tanı](#tanı)
7. [Geçmiş okuma](#geçmiş-okuma)
8. [Kapsam eşlemeleri](#kapsam-eşlemeleri)
9. [Belirtiler](#belirtiler)
   (OPS-R1 ... OPS-R65; `doctor` satırındaki `runbook: OPS-R30` yazısı 30. girişi gösterir)

`install`, `status`, `logs`, `doctor`, `backup`, `restore`, `upgrade`, `rollback`,
`repair-state`, `support-bundle` ve `bundle` (çevrimdışı paket) çalışır.

---

## Kurulum

Kurulum tek komuttur. Kayıt defterinden (registry) kuran olağan kurulum alt modül istemez:

```bash
git clone https://github.com/wiemBe/Behavior-Analytics.git
cd Behavior-Analytics
./qradar-dash install
```

`--recurse-submodules` yalnız `install --build` için gerekir: `qradar-mcp` alt modülünü çeker. Kayıt
defterinden kuran bir kurulum alt modül olmadan çalışır.

```bash
git clone --recurse-submodules https://github.com/wiemBe/Behavior-Analytics.git
cd Behavior-Analytics
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
| `--offline BUNDLE` | İmajları çevrimdışı paket tar'ından yükle (paketi `bundle` yazar; bkz. [Çevrimdışı paket](#çevrimdışı-paket)). |
| `--external-proxy` | Paketle gelen TLS vekilini atla; kendi vekilini sen işlet. |
| `--reconfigure` | Yanıtlar kayıtlı olsa bile soruları yeniden sor. |
| `--dry-run` | Her kontrolü çalıştır, hiçbir şey yazma, hiçbir şey değiştirme. |
| `--skip-qradar-check` | QRadar konsoluna TLS el sıkışması testini atla. |

### Operatör hesabı ve yedek dizini

Kurulum ve işletme için ayrılmış bir hesap kullan; günlük işi `root` ile yapma. Hesap, `docker`
komutunu çalıştırabilmelidir: `install`, `status`, `doctor`, `backup`, `restore`, `upgrade` ve
`rollback` tümü `docker compose` çağırır. Bu yüzden hesabı `docker` grubuna ekle.

**Sıra önemlidir.** `docker` grubu, Docker kurulunca oluşur. Önce Docker Engine ve Compose v2
eklentisini Docker'ın RHEL deposundan kur (Rocky ve Alma dahil), sonra hesabı aç:

```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
docker compose version   # Compose v2 eklentisini doğrular
```

Docker kurulmadan `useradd -G docker` "group 'docker' does not exist" ile düşer.

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

- **En az:** 4 vCPU, 8 GiB RAM, **Docker kökünde 80 GiB boş** disk. Bu ana makine Phase A ile
  varsayılanda açık aileler (catch-all, geo, explanations) içindir. Bunlar Administration →
  Search cadence sayfasından değiştirilmedikçe 5 dakikada bir aranır. WAF, IPS, kural eşleşmesi
  toplama, özel kurallar ve mülk izleme (property watch) kapalı bırakılmıştır.
- **Önerilen:** 8 vCPU, 16 GiB RAM, Docker ve Postgres için 400 GiB ve yedek dizini olarak bağlanmış
  **ikinci bir dosya sistemi** (400 GiB). İkinci dosya sistemi, veri diski dolduğunda bile yedeğe
  erişmeni sağlar. Yedek dizini ile Docker veri kökü aynı aygıttaysa kurulum ön kontrolü uyarır,
  reddetmez. Aynı aygıttaki yedek hatalı bir migrasyondan kurtarır, ölü bir diskten kurtarmaz.
- Retention (saklama süresi): `RETENTION_ENABLED` varsayılanda kapalıdır ve yalnız dört Timescale
  parça politikasını kapatır (`log_source_metric`, `search_result_metric`, `rule_metric`,
  `offense_snapshot`). Kurulum sihirbazı `RETENTION_ENABLED=true`, bu dört tablo için 90 gün ve
  sıkıştırma için 7 gün yazar. Politikalar `apply_timescale_policies` görevi çalışana kadar
  uygulanmaz. Günlük satır silicileri bu bayraktan bağımsız çalışır: arama sonuçları 90 gün,
  catch-all 35, coğrafya 35, WAF metrik 35 / kapalı olay 180, IPS metrik 35 / kapalı olay 365,
  property-watch metrik 35 / kapalı olay 180, anomaly fault 30, özel kural ihlali 180, kapalı
  Phase A olay 365, `rule_health_snapshot` 90, `detection_coverage_snapshot` 90,
  `rule_match_metric` ve `rule_match_window` 90. Diski izle (bkz. OPS-R14).

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
için kullanılır. Dosya varsa `install` ve `upgrade` onu üretim overlay'inden sonra gerçekten yükler,
bilgi satırında adını yazar ve `.env` içindeki `COMPOSE_FILE` listesine ekler; silmez. QRadar:443'e
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
14. QRadar'ın sakladığı geçmişin okunup okunmayacağı. "Read the last 28 days of history from QRadar
    now, so baselines are steadier from day 1? (yes/no)". Soru yalnız ilk kurulumda sorulur: kayıtlı
    yanıt varsa ya da sitede `.qradar-dash/state.json` varsa sorulmaz. Yanıt dosyasındaki anahtar
    `HISTORY_BACKFILL` olup `yes` ya da `no` değerini alır; dosyada yoksa konsol kararsız kalır ve
    ilk yönetici oturum açmasında soru sorulur. Ayrıntı: [Geçmiş okuma](#geçmiş-okuma).

Sorulmayanlar: LDAP (arayüzden yapılır), dedektör eşikleri, aile etkinleştirme, SMTP, webhook, LLM.
WAF, IPS, kural eşleşmesi toplama, özel kurallar ve mülk izleme (property watch) varsayılanda
**kapalı** kalır. Phase A her zaman açıktır. Catch-all, geo ve explanations varsayılanda açıktır.
Kurulum özeti açık aileleri ve kapalı aileleri ayrı satırlarda yazar. Açık aileler, Administration →
Search cadence sayfasından değiştirilmedikçe 5 dakikada bir aranır. WAF veya IPS açıksa özet, onların
o sayfada değiştirilene kadar 15 dakika kullandığını da yazar.

**Mülk izleme (Phase G).** İki anahtar vardır: `PW_COLLECTION_ENABLED` (varsayılan **false**) aile
anahtarı, `PW_ALERTING_ENABLED` (varsayılan **false**) gölge moddur; gölge modda olaylar ve yaşam
döngüsü yazılır ama hiçbir şey sayfa açmaz. Üst sınırlar: `PW_MAX_ENABLED_WATCHES` 10, tick başına
`PW_MAX_SEARCHES_PER_TICK` 40, izleme başına `PW_MAX_SEARCHES_PER_WATCH_PER_TICK` 12, izleme başına
etkin değer `PW_MAX_ACTIVE_VALUES_PER_WATCH` 200, sabitlenmiş (pinned) değer `PW_MAX_PINNED_VALUES`
10. En yüksek sıklık `PROPERTY_WATCH` ailesine `/admin/search-cadence` sayfasından verilir (5 dk –
8 sa, varsayılan 5 dk). Tüm `pw_*` ayarları ve varsayılanları README'deki "Property watches"
bölümündedir. Bir izleme kendi geçmişini ayrıca isteyebilir; bunun anahtarı `PW_BOOTSTRAP_ENABLED`
(varsayılan **true**), ayrıntı [Geçmiş okuma](#geçmiş-okuma).

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

8b. Geçmiş sorusunun yanıtını kaydet (yalnız `HISTORY_BACKFILL` yanıtı verildiyse):

    ```bash
    docker compose run --rm --no-deps -T backend \
      python -m app.cli.history_backfill decide \
        --instance <name> --answer yes --source installer
    ```

    Bu adım ilk toplamadan önce çalışır, böylece ilk tick kararı görür. Başarısız olursa kurulum
    **durmaz**: konsol kararsız kalır, özet "not recorded (exit N)" yazar ve ilk yönetici oturum
    açmasında soru sorulur. Kayıtlı bir karar varsa komut onu değiştirmez.

9. Vekil (harici değilse), backend, frontend, `celery-worker` ve `celery-beat` başlar. `mcp` profili
   başlamaz.
10. Bu servislerin sağlıklı olması (migrate için 0 koduyla çıkması) beklenir. Bekleme süresi 180 sn.
11. Ana makineden `GET http://127.0.0.1:8000/api/v1/health/ready` ve
    `GET http://127.0.0.1:8000/api/v1/version`.

Bir test VM'de ilk kurulum toplam 1 dk 40 sn sürdü (QRadar erişilemezken). Bu bir söz değildir.

### Özet ve ilk oturum açma

Özet en sonda yazdırılır: URL `https://<ad>/`, oturum açma yolu `/login`, yönetici kullanıcı adı ve
"parola yukarıda yazdırıldı ve saklanmıyor" cümlesi, QRadar bağlantı sonucu, geçmiş okuma yanıtı
(kaydedildiyse yanıtın kendisi, kaydedilmediyse "not decided; the first administrator sign-in will
ask"), kapalı aileler (WAF, IPS, kural eşleşmeleri, özel kurallar), yedek dizini ve bu kılavuzun
yolu `docs/OPERATIONS.md`.

- Çıkış kodu 0: yığın sağlıklı, `/health/ready` ve `/version` 200 döndürdü, QRadar kontrolü geçti ve
  yönetici var.
- Çıkış kodu 1: ön kontrol ya da çalışma adımı başarısız, QRadar kontrolü başarısız veya
  `/health/ready` ya da `/version` 200 döndürmedi ya da yayımlanan URL denetimi başarısız oldu (aşağıda).
  Parola bu çıkıştan önce stdout'a yazılmış olabilir;
  başarısız QRadar kontrolü onu yutmaz. Özet aşamasına ulaşıldıysa QRadar veya API hatasında durum
  dosyası yine yazılır ve özet `API readiness      NOT READY` satırını gösterir.
- Çıkış kodu 2: seçenek ya da etkileşimsiz yanıt dosyası geçersizdir; kurulum başlamaz.
- Çıkış kodu 3: harici vekilde güvenli bir `FORWARDED_ALLOW_IPS` yoktur; backend başlatılmaz.

  QRadar kontrolü başarısız olduğunda yığın ve `.qradar-dash/state.json` **yerindedir**: kurulum
  "başarısız" görünse de `status`, `upgrade` ve `rollback` çalışır. QRadar'ı düzelt ve `install`
  komutunu yeniden çalıştır (`qradar add` satırı günceller); ya da `./qradar-dash status` ile duruma
  bak. Yığın ayağa kalkmadıysa (bir servis hiç sağlıklı olmadı) `.qradar-dash/state.json` yazılmaz;
  `docker compose ps` ve `./qradar-dash logs` ile bak.

Özetten önce `install`, yayımlanan URL'yi de dener (yalnız paketle gelen vekilde; harici vekilde
(`EXTERNAL_PROXY=yes`) bu denetim yoktur): `https://<ad>/api/v1/health/ready` adresine, ana makinedeki
vekile (`--resolve` ile `127.0.0.1`) 15 saniye sınırıyla gider. 200 gelirse `public URL … answered
HTTP 200 through the local proxy` yazılır. Sertifika doğrulanamıyorsa (curl çıkışı 60 ya da 51;
kendinden imzalı ya da henüz güvenilmeyen sertifika) betik aynı isteği sertifika denetimi kapalı
yineler. Şimdi 200 gelirse `[warn]` yazar ve `install` **0 ile çıkar**:

```
public URL https://<ad>/api/v1/health/ready answered HTTP 200 only with certificate checks disabled (curl exit 60 or 51). Browsers will warn unless the site CA is trusted. Continuing.
```

`[warn]` satırı stderr'e gider. Bu bir arıza değildir; yığın sağlıklıdır, tarayıcılar site CA'sı güvenilir olana kadar uyarır.
Sertifika denetimi kapalıyken de 200 gelmezse (ya da hata başka bir curl kodudur) `public URL …
failed (curl exit …, HTTP …). Next: ./qradar-dash logs proxy and ./qradar-dash doctor.` yazılır;
özet yine basılır, ama çıkış kodu 1 olur.

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

- Saklanan sırlar `SECRET_KEY`, `POSTGRES_PASSWORD`, `ENCRYPTION_KEY`, `ENCRYPTION_KEYS_JSON` ve
  `ENCRYPTION_KEY_VERSION` değerleridir; değerleri bilgi satırına yazılmaz. `ENCRYPTION_KEY`'i
  değiştirmek saklı şifreli veriyi okunmaz yapar. `.env` içindeki `POSTGRES_PASSWORD`'i değiştirmek
  hazır bir volume'un içindeki parolayı değiştirmez.
- `LOG_LEVEL`, `AUTH_PROVIDER`, bütün `RETENTION_*` anahtarları, `COMPRESSION_AFTER_DAYS`,
  `LLM_ENABLED` ve `MCP_ENABLED` varsa korunur; yoksa kurulum varsayılanı yazılır. Kurucunun
  tanımadığı diğer `KEY=VALUE` satırları (örneğin `NOTIFY_*`, `SMTP_*` ve aile anahtarları) eski
  sıralarıyla dosyanın sonundaki tek bir "Kept from" bölümüne taşınır. Yorumlar ve boş satırlar
  taşınmaz; hiçbir anahtar iki kez yazılmaz.
- Sihirbaz yanıtlarından türetilen adres, port, Compose, imaj, volume ve QRadar alanları kayıtlı
  yanıtlardan yeniden yazılır. Üretim değişmezleri (`ENVIRONMENT=production`, `DEBUG=false`,
  `QRADAR_PROVIDER=rest`, `QRADAR_VERIFY_SSL=true`, iç token/CA yolları, Postgres adları, API
  yolları ve `LLM_ALLOW_AUTONOMOUS_ACTIONS=false`) her seferinde zorlanır; eski değer farklıysa
  anahtarın eski ve yeni değeriyle uyarı yazılır.
- Kullanıcı adı zaten varsa `create-admin` çağrılmaz. Tekrar çağırmak parolayı sıfırlar ve
  oturumları iptal eder.
- `load-secrets.sh` ve `qradar add` yeniden çalışır; migrate'te Alembic zaten başta olduğundan bir
  şey yapmaz; `up -d` yeniden çalışır.
- `COMPOSE_PROFILES` içinde `proxy` sihirbaz yanıtına göre yazılır; elle eklenmiş diğer profiller
  (pratikte `mcp`) korunur. `upgrade` ve `rollback`, `mcp` açıksa `qradar-mcp` servisini de hedef
  imajla yeniden yaratır (bkz. OPS-R64).

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

## Çevrimdışı paket

Üretim makinesi kayıt defterine (registry) ulaşamıyorsa imajlar ve checkout, internete çıkan bir
makinede tek dosyaya paketlenir ve elle taşınır. Paketi `bundle` yazar; `install --offline` ve
`upgrade --offline` okur. Üretim makinesinde `docker pull` ve `git` gerekmez.

### 1. Bağlı makinede paketi hazırla

Paketlenecek sürümün checkout'unda ve `docker pull` yapabilen bir makinede:

```bash
git fetch --tags
git checkout v<sürüm>
./qradar-dash bundle
```

Seçenekler: `--output DIR` paketi başka bir dizine yazar (varsayılan: bulunduğun dizin).
`--version X.Y.Z` yalnız bir doğrulamadır: `VERSION` dosyasıyla aynı olmak zorundadır, aksi halde
`bundle refused: this checkout is version <checkout>, not <sürüm>; run git fetch --tags && git checkout v<sürüm> first`
yazılır. Betik imaj ön ekini `.env` içindeki `QRADAR_DASH_IMAGE_PREFIX` değerinden alır; `.env`
yoksa varsayılan ön eki kullanır ve çıktıda hangisini kullandığını yazar.

Komut yedi imajı çeker: `postgres`, `backend`, `frontend`, `proxy` ve `qradar-mcp` (hepsi seçilen
sürümde), `docker-compose.prod.yml` dosyasında digest ile sabitlenmiş `redis` ve
`deploy/load-secrets.sh` içinde yine digest ile sabitlenmiş yardımcı `alpine` (gizli dizileri
yükleyen ve yedekleyen imaj; çevrimdışı makine onu çekemeyeceği için pakette taşınır). Redis ve
yardımcı imajın başvurusu kendi dosyasından okunur, betikte elle yazılı değildir. Özel bir kayıt
defteri için önce `docker login ghcr.io` (`read:packages` token) çalıştırılır; çekme hatası imajı
adıyla söyler.

`qradar-dash-<sürüm>-bundle.tar` şunları içerir:

| Üye | Anlamı |
|---|---|
| `images.tar` | Yedi imajın `docker save` çıktısı (redis ve yardımcı `alpine` dahil). |
| `SHA256SUMS` | Bu dosya dışındaki her üyenin özeti. |
| `MANIFEST` | Her imaj başvurusu ve paket yapılırken yerel imaj kimliği; alıcı taraf yüklediğini bununla denetler. |
| `VERSION`, `docs/releases/<sürüm>.md` | Sürüm ve sürüm notları. |
| `qradar-dash`, `docker-compose.yml`, `docker-compose.prod.yml`, `deploy/` | Üretimde `git` olmadan kullanılacak checkout. |

Pakete `.env`, `.secrets/` ve `docker-compose.override.yml` hiçbir zaman girmez. Paket önce geçici
bir adla yazılır, sonunda yerine taşınır; hata olursa dizinde yarım paket kalmaz ve betik bunu
söyler. Aynı adlı bir paket varsa üzerine yazılmaz (`bundle refused: … already exists`). Bitince
betik yolu, boyutu ve üretim makinesinde çalıştırılacak komutları yazar.

Dosyayı taşıdıktan sonra iki makinede de `sha256sum qradar-dash-<sürüm>-bundle.tar` çalıştır;
çıktılar aynı olmalıdır.

### 2. Üretim makinesinde ilk kurulum

Checkout'u paketin içinden çıkar (imajlar `images.tar` içindedir; çıkarmaya gerek yoktur) ve kur:

```bash
mkdir qradar-dash-<sürüm>
tar -xf /yol/qradar-dash-<sürüm>-bundle.tar -C qradar-dash-<sürüm> --exclude=images.tar
cd qradar-dash-<sürüm>
./qradar-dash install --offline /yol/qradar-dash-<sürüm>-bundle.tar
```

Göreli yol da kabul edilir ve komutu çalıştırdığın dizine göredir. Kurulum yanıtları her zamanki gibi sorulur ya da
`--non-interactive FILE` ile verilir.

### 3. Kurulu sitede yükseltme

Çıkardığın dosyalar mevcut kurulum dizininin üzerine yazılır (`.env`, `.secrets/` ve
`docker-compose.override.yml` pakette olmadığı için değişmez); sonra yükseltme paketle çalışır:

```bash
cd <kurulum dizini>
tar -xf /yol/qradar-dash-<sürüm>-bundle.tar --exclude=images.tar
./qradar-dash upgrade <sürüm> --offline /yol/qradar-dash-<sürüm>-bundle.tar
```

Sıra aynıdır: önce toplam denetimi ve sürüm denetimi, sonra imajların yüklenmesi, sonra her zamanki
yükseltme. Hiçbir konteyner imajlar yüklenip denetlenmeden durdurulmaz. Paket vermeden
`upgrade <sürüm> --offline`, imajların daha önce yüklenmiş olmasını şart koşar; eksik olan ilk imajı
adıyla söyler ve paketi vermeni ister.

### Paketin yaptığı denetimler

Yükleme başlamadan şunlardan biri tutmazsa `nothing was loaded` yazılır ve hiçbir imaj yüklenmez:

| Denetim | Mesajın başı |
|---|---|
| Dosya tar değil ya da `VERSION`/`MANIFEST` yok | `cannot read … as a tar file` / `has no VERSION member` |
| Üyede bağlantı (link) ya da `..` ve mutlak yol var | `holds links` / `holds absolute or parent-directory paths` |
| Paket sürümü, checkout (install) ya da yükseltme hedefi ile aynı değil | `bundle refused: … is version A but this checkout is B` |
| Paketteki imaj ön eki sitenin ön ekiyle aynı değil | `bundle refused: its images are under A but this site uses B` |
| `sha256sum -c` tutmuyor ya da özetlenmemiş bir üye var | `the bundle checksums do not match` / `which SHA256SUMS does not cover` |

Çözüm hep aynıdır: paketi bağlı makineden yeniden kopyala ya da doğru sürümün/ön ekin paketini yap.

`docker load` sonrasında `MANIFEST` içindeki her başvuru için `docker image inspect` çalışır. Kimlik
(ID) karşılaştırması yalnız paketi yapan makine ile bu makinenin Docker imaj deposu (image store)
aynı türdeyse yapılır; tür `MANIFEST` içindeki `image_store` satırında (`containerd` ya da
`classic`) yazılır, çünkü `.Id` iki depoda farklı anlama gelir. Türler farklıysa betik şunu yazar ve
sürer; bütünlük `images.tar` için yapılan `SHA256SUMS` denetimine dayanır, her başvurunun çözülmesi
yine şarttır:

```
Image IDs were not compared: the bundle host's image store is containerd and this host's is classic; integrity rests on the SHA256SUMS check of images.tar. Every image reference is still checked.
```

`image_store` satırı olmayan eski bir paketin kimlikleri her zaman karşılaştırılır.

Redis digest ile sabitlenmiştir (`redis:7.4-alpine@sha256:…`). `docker save` böyle bir başvurunun
adını kaydetmez; `docker load` sonrası imaj yalnız kimliğiyle kalır ve Compose onu bulamaz, çekmeye
kalkar. Docker bir digest başvurusunu etiket hedefi olarak da reddeder (`refusing to create a tag
with a digest reference`), bu yüzden betik yalnız düz etiketi koyar ve ardından sabitlenmiş
başvurunun çözüldüğünü doğrular:

```
[ ok ] docker load kept redis:7.4-alpine@sha256:… only by its ID; set the tag redis:7.4-alpine on it (this moves an existing redis:7.4-alpine on this host)
```

Bu, bu makinede zaten var olan bir `redis:7.4-alpine` etiketini yüklenen imaja taşıyabilir; bir
başka imajı gösteriyorsa o imaj etiketsiz kalır (silinmez). Düz etiket konduktan sonra da sabitlenmiş
başvuru çözülmezse (yüklenen imaj bu Docker'da sabitlenmiş digest'i taşımıyor) betik durur ve
hiçbir şey başlatmaz:

```
after docker load, redis:7.4-alpine@sha256:… does not resolve: the loaded image (sha256:…) does not carry the pinned digest on this Docker, even with the tag redis:7.4-alpine set. Make the bundle again with ./qradar-dash bundle on a connected host whose Docker image store is the same as this host's (…)
```

Çözüm: paketi, imaj deposu bu makineyle aynı türde olan bağlı bir makinede yeniden yap
(`docker info` deposu gösterir). Elle yazılacak bir `docker tag … @sha256:…` komutu yoktur; Docker onu
reddeder.

Aynı mekanizma gizli dizilerin yardımcı imajı için de geçerlidir (`alpine:3.20@sha256:…`;
sabitleme `deploy/load-secrets.sh` içindedir). `docker load` onu da yalnız kimliğiyle tutabilir;
betik düz `alpine:3.20` etiketini koyar ve sabitlenmiş başvurunun çözüldüğünü doğrular. Yükleme
şunu yazar (`…` yerinde paketteki digest vardır):

```
[ ok ] docker load kept alpine:3.20@sha256:… only by its ID; set the tag alpine:3.20 on it (this moves an existing alpine:3.20 on this host)
```

Redis için söylenen burada da geçerlidir: bu makinede zaten var olan bir `alpine:3.20` etiketi
yüklenen imaja taşınabilir; bir başka imajı gösteriyorsa o imaj etiketsiz kalır (silinmez).
Sabitlenmiş başvuru düz etiketten sonra da çözülmezse betik redis'tekiyle aynı biçimde durur
(`after docker load, alpine:3.20@sha256:… does not resolve: …`). Paketsiz
`upgrade <sürüm> --offline` bu imajı da arar: yükseltme öncesi yedek onu çalıştırır, eksikse betik
imajı adıyla söyler ve hiçbir konteyneri durdurmaz.

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
./qradar-dash upgrade <version> [--offline [BUNDLE]]
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
yoktur. `--offline`, çekmek yerine hedef imajların yerelde yüklü olmasını şart koşar;
`--offline BUNDLE` önce paketi doğrular ve yükler (bkz. [Çevrimdışı paket](#çevrimdışı-paket)).

`upgrade` ve `rollback`, `doctor` raporundan yalnız `db_connect` ve `migration_head` satırlarını
yazdırır; ardından `Full report: ./qradar-dash doctor` satırı gelir:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0025
Full report: ./qradar-dash doctor
```

Raporun geri kalanı (henüz yazılmamış bir beat kalp atışı, QRadar kontrolleri) yükseltmeyi ya da
geri almayı yanlış bir başarısızlık gibi gösterirdi; bu yüzden yalnız karar veren iki satır yazılır.

Bu çift `upgrade` tarafından **iki kez** yazılır; her ikisinden sonra da
`Full report: ./qradar-dash doctor` satırı gelir:

- **Birincisi** yazıcılar durmadan önce, ön kontrolde yazılır ve **kurulu** sürümün başını
  gösterir (`database and image are at 0025`). Bu, veritabanının yükseltmeden önce sağlıklı
  olduğunun kanıtıdır.
- **İkincisi** yeni yığın başladıktan sonra yazılır ve **hedef** sürümün başını gösterir
  (`database and image are at 0030`). Bu, migrasyonun hedef başa ulaştığının kanıtıdır.

`rollback` çifti **bir kez** yazar: geri alınan sürüm ayakta olduktan sonra, o sürümün başında
(`database and image are at 0025`). Bir yükseltme otomatik geri alınırsa geri almanın kendi
kontrolü de çifti bir kez daha yazar; o zaman son çift geri alınan sürümün başını gösterir.
Kurulu sürüm zaten hedefse (`Version <sürüm> is already installed and healthy; nothing changed.`)
çift yalnız bir kez yazılır.

### İmaj ön ekinin taşınması

Depo 2026-10-18'de `wiemBe/QRadar-Dashboard` adından `wiemBe/Behavior-Analytics` adına taşındı.
Sonraki sürümlerin imajları `ghcr.io/wiembe/behavior-analytics/<servis>` altında yayımlanır; yayın
iş akışı, depo adından imaj adını türetir, yani sonraki her sürüm yeni adda çıkar. `0.1.0` ve
`0.1.1` yayımlanırken depo hâlâ eski addaydı; bu iki sürüm 2026-10-20'de `mirror-images` iş akışıyla
yeni ada kopyalandı. Bugün `ghcr.io/wiembe/behavior-analytics/<servis>:0.1.0` ve `:0.1.1` de anonim
olarak çekilebilir ve her iki addaki imajın özeti aynıdır.

`upgrade`, taşımayı kendisi yapar. Hedef sürümün betiği, sürüm kontrollerinden ve durum dosyası
kontrollerinden sonra, hiçbir şey çekmeden önce çalışır:

- `.env` içindeki `QRADAR_DASH_IMAGE_PREFIX` tam olarak eski ön ek ise ya da anahtar hiç yoksa
  (eski compose varsayılanı geçerliydi) taşıma yapılır. Başka her değer operatörün kendi kayıt
  defteri ya da yansısıdır; dokunulmaz ve betik tek satırla hedef imajların o ön ek altında
  bulunması gerektiğini söyler.
- Önce kurulu sürümün imajları (`postgres`, `backend`, `frontend`; profil açıksa `proxy` ve
  `qradar-mcp`) yerelde yeni ön ek altında etiketlenir; böylece çalışan yığın çevrimdışı yeniden
  oluşturulabilir. Gerekli bir imaj yerelde yoksa yükseltme, imajı adıyla anarak reddedilir ve hiçbir
  şey değişmez.
- Sonra `.env` içindeki `QRADAR_DASH_IMAGE_PREFIX` ve (varsa) `.secrets/install.answers`
  içindeki `IMAGE_PREFIX` yeni değerle yeniden yazılır (dosya kipi 0600 kalır) ve şu satır yazılır:

```
Image prefix moved: ghcr.io/wiembe/qradar-dashboard -> ghcr.io/wiembe/behavior-analytics (the repository was renamed).
```

Taşımadan sonra yükseltme başarısız olursa (çekme reddedilir, otomatik geri alma çalışır) taşıma
yerinde kalır; yerel etiketler sayesinde durum tutarlıdır. `rollback` kayıtlı imaj kimliklerini
**geçerli** `.env` ön ekiyle etiketler ve `.env` dosyasını eski haline döndürmez; yani geri alınan
sürüm de yeni ön ek altında çalışır. Eski bir sürümü yeni ön ek altında çekmek gerekirse (geri
almadan sonra aynı sürümün `install` yeniden çalıştırması), imajlar `mirror-images` iş akışıyla
(`.github/workflows/mirror-images.yml`, elle çalıştırılır) yeni ada kopyalanmış olmalıdır. Bu iş
akışı yalnız **taşımadan önce** yayımlanmış sürümler için gerekir: `0.1.0` ve `0.1.1`
2026-10-20'de kopyalandı, sonraki sürümler zaten yeni adla yayımlanır. Yeni bir sanal makinede
(2026-10-20) kopyalama sonrasında geri alınan sürümün `install` yeniden çalıştırması denendi:
kurulum tamamlandı, yönetici parolası korundu ve `ENCRYPTION_KEY` döndürülmedi.
`upgrade --offline` taşımayı aynı şekilde yapar; hedef imajlar yeni ön ek altında yüklü olmalıdır.

### Ön kontrol

Hiçbir konteyner durdurulmadan önce, betik kontrolleri şu sırayla yapar:

- Checkout'un kendisi hedef sürümdür (bkz. yukarıdaki Yükseltme bölümü): `VERSION` dosyasının
  içeriği hedefle aynı olmalıdır. Değilse
  `upgrade refused: this checkout is version <checkout>, not <hedef>; run git fetch --tags && git
  checkout v<hedef> first (see docs/OPERATIONS.md, Yükseltme)` ile reddedilir. Bu, ilk kontroldür:
  reddedişten önce hiçbir şey çekilmemiş ve hiçbir konteyner durdurulmamıştır.
- `.qradar-dash/state.json` vardır ve geçerlidir; değilse betik geçersiz ya da eksik alanı
  adıyla bildirir (`upgrade refused: .qradar-dash/state.json field <alan> is invalid or missing`).
- `installed_version` hedeften eskidir. Aynı sürüm ve sağlıklı konteynerlerde komut çalışma
  kontrollerini geçer, `Version <sürüm> is already installed and healthy; nothing changed.`
  yazar ve 0 ile çıkar; aynı sürüm sağlıklı değilse
  `version <sürüm> is recorded but the running stack is not healthy: <neden>` ile durur.
  Daha yeni kurulu sürüm `upgrade refused: <kurulu> is newer than <hedef>; that is rollback` ile
  reddedilir.
- Önceki yükseltme `outcome=failed` kaydettiyse, `rollback` ya da başarılı bir `restore` bunu
  temizleyene kadar yeni yükseltme `upgrade refused: the last upgrade failed; run rollback or
  restore first` ile engellenir. `last_upgrade.restored_from` alanı dolu olan `failed` bir kayıt
  bu engeli kaldırır (başarılı bir `restore` o alanı yazar).
- İmaj ön eki taşınır (bkz. İmaj ön ekinin taşınması). Bu da hiçbir şey çekilmeden önce olur:
  kurulu imajlar yeni ön ek altında etiketlenir, `.env` ve `.secrets/install.answers` yeniden
  yazılır ve `Image prefix moved: …` satırı yazılır. Ön ek başka bir kayıt defterine ya da aynaya
  aitse hiçbir şey değişmez; betik, hedef imajların o ön ek altında bulunması gerektiğini söyler.
  Kurulu imaj yerelde yoksa yükseltme, imajı adıyla anarak reddedilir ve hiçbir şey değişmez.
- Hedef imaj çekilebilir ya da zaten yüklüdür. Çekme, yazıcılar durmadan **önce** olur; kayıt
  defteri hatası kesinti yaratmaz. Çekilemezse
  `upgrade refused: target images are unavailable; no container was stopped`. `--offline`
  verilmişse çekme yerine her hedef imajın yerelde olduğu doğrulanır; biri yoksa
  `upgrade refused: target images are unavailable: image <imaj> is not loaded on this host; no container was stopped; pass the bundle: ./qradar-dash upgrade <sürüm> --offline /path/to/qradar-dash-<sürüm>-bundle.tar`
  yazılır. Paket verilmişse önce toplamlar ve sürüm denetlenir, imajlar yüklenir ve `MANIFEST`
  ile karşılaştırılır; bunların hiçbiri bir konteyneri durdurmaz.
- Hedef imajın sürüm notları ayrıştırılır ve `installed_version`, `min_from` değerine eşit ya da
  büyüktür. Ayrıştırılamazsa
  `upgrade refused: target release notes are missing, invalid, or for another version`; eskiyse
  `upgrade refused: <hedef> requires at least <min_from>`. Bu reddediş zararsızdır: komut 1 ile
  çıkar, hiçbir konteyner durmaz ve `state.json` değişmez. Yalnız iki şey olmuştur: imaj ön eki
  taşınmış ve hedef imajlar çekilmiştir. Örneğin `0.1.0` kurulumunda `upgrade 0.1.2`,
  `upgrade refused: 0.1.2 requires at least 0.1.1` ile reddedilir; sonraki adım
  `git checkout v0.1.1 && ./qradar-dash upgrade 0.1.1` ise taşınmış ön eki okur ve `0.1.1`
  imajlarını yeni ad altında çeker. Bu, eski sürümler yeni ada kopyalandığı için çalışır
  (bkz. İmaj ön ekinin taşınması).
- Disk koruması (bkz. Yedek ve geri alma) geçer. Yedek kökü yoksa
  `upgrade refused: backup root is unavailable`; veritabanı boyutu okunamazsa
  `upgrade refused: database size is unavailable`. Koruma
  `Backup disk: <boş> GiB free; <gerekli> GiB required at <yol>.` yazar, yer yetmiyorsa
  `backup disk guard refused: <boş> GiB free, <gerekli> GiB required at <yol>` ile reddeder.
- `doctor` `db_connect` ve `migration_head` **PASS** verir. Biri FAIL ise
  `upgrade refused: database readiness is already failing; see runbook OPS-R11`; biri hiç PASS
  bildirilmediyse `upgrade refused: doctor did not report db_connect and migration_head as PASS;
  see runbook OPS-R11`. Veritabanı **mevcut** imajın başında değilse biri elle migrate etmiştir
  ya da önceki migrate çökmüştür. Dur ve OPS-R11'e bak. Bu kontrol yazıcılar durmadan önce
  çalışır ve **kurulu** sürümün başını gösterir.
- Çalışan imaj özetleri okunabilir; okunamazsa
  `upgrade refused: running image digests are unavailable`.
- `.env` içindeki `QRADAR_DASH_VERSION`, durum dosyasındaki `installed_version` ile aynıdır.
  Değilse `upgrade refused: .env has QRADAR_DASH_VERSION=<değer> but the state file says
  <kurulu>`.
- UTC saati alınabilir; alınamazsa `upgrade refused: the UTC time is unavailable`.

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

Yeni bir sanal makinede (2026-10-20, 4 vCPU / 10 GiB) ölçülenler: `0.1.1` → `0.1.2` yükseltmesi
52 sn sürdü, kesintisi 16,2 sn; geri alma 33 sn sürdü, kesintisi 13,7 sn; ikinci yükseltme 50 sn
sürdü, kesintisi 20,8 sn. Toplam süre, kesinti penceresinin dışındaki imaj çekme ve ön kontrol
adımlarını da kapsar. Bunlar da bir söz değildir.

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
`previous_images` `null` ise `rollback` reddeder. Başarılı bir yükseltmeden sonra `previous_images`
dolu kalır, çünkü `rollback` onu okur; yalnız `rollback` onu `null` yapar. `status` içindeki
`Last upgrade backup … (age …)` yaşı, yedek dizininin adındaki UTC damgasından hesaplanır.

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
- Yükseltmeden sonra açılan bir servisin (örneğin `mcp` profilinin) kayıtlı imajı yoktur. Geri alma
  eski sürüm etiketini kullanır ve çevrimiçi sunucuda imajı çeker; çevrimdışı sunucuda ise eski
  paketin imajları yüklenene ya da profil `COMPOSE_PROFILES` içinden çıkarılana kadar hiçbir şeyi
  değiştirmeden reddeder.
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
3. Önceki imaj özetlerini başlatır; özetleri önceki sürümün etiketlerine çevirir. Kayıtlı özeti
   olan imajları çekmez; özetler yereldir. Kayıtlı imajı olmayan bir servisin (yukarıdaki ret
   listesinin `mcp` maddesi) imajını yalnız çevrimiçi sunucuda, önceki sürümün etiketiyle çeker.
4. Yükseltmedeki aynı sağlık kontrolleri, bu kez **önceki** sürümü bekleyerek. Bu kontrol
   `doctor` raporundan yalnız `db_connect` ve `migration_head` satırlarını **bir kez** yazar
   (`Full report: ./qradar-dash doctor` satırıyla birlikte) ve **geri alınan** sürümün başını
   gösterir.
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
PASS  db_connect         database accepted SELECT 1
WARN  cadence            enabled families late or never run: WAF_COLLECTION    runbook: OPS-R30
FAIL  migration_head     database at 0031, image expects 0032    runbook: OPS-R11
```

Bu üç satır, konteyner içi kontrollerin tam kimlik listesi `render_text` ile hizalanınca çıkan
genişliktir. En uzun kimlik `anomaly_evaluation` (18 karakter) olduğu için sütun 18'dir. Ana makine
satırları ayrıdır ve 16 karakterlik sabit genişlik kullanır.

`PASS`, `SKIP`, `WARN`, `FAIL` vardır. `SKIP`, ilgili özellik kapalı olduğu için denetimin
uygulanmadığını söyler ve çıkış kodunu yükseltmez. Neden tek satırdır. Kılavuz kimliği `OPS-R` ile giriş
numarasıdır (`OPS-R30` 30. girişi gösterir). Çıktıda token, `.env` değeri, DSN ya da
`ENCRYPTION_KEY` yoktur. Bağlantı hataları ana makineyi ve istisna sınıfını söyler, parolayı değil.

Çıkış kodu: tümü PASS ise 0; FAIL olmadan en az bir WARN varsa 1; en az bir FAIL varsa 2.
`./qradar-dash doctor --json` tek bir JSON dizisi yazar: önce ana makine kontrolleri, sonra
konteynerin kendi `--json` çıktısı. `-h` ve `--help` kullanımı yazdırır ve 0 ile çıkar. Bilinmeyen
argüman (`doctor --json extra` dahil) kullanımı yazdırır ve 2 ile çıkar.

`doctor` hiçbir yazma işlemi yapmaz. Tek QRadar çağrısı `GET /system/about` isteğidir. QRadar'a ayrı
bir TLS el sıkışması CA paketiyle yapılır ve `SEC` başlığı göndermez; sertifika süresi token
gösterilmeden okunur. `doctor` senkronizasyon başlatmaz, Ariel araması çalıştırmaz ve LDAP'a
bağlanmaz.

**Ana makine kontrolleri**

| Kimlik | Ne yapar | OPS-R |
|---|---|---|
| `disk_docker` | Docker kökünün `df -P` değeri. %70 ve üstü WARN, %90 ve üstü FAIL. | OPS-R14 |
| `disk_backup` | `QRADAR_DASH_BACKUP_DIR` için aynı eşikler ve disk koruması. Veritabanı boyutu okunamazsa uyarır. | OPS-R14 |
| `host_clock` | Bu ana makinenin NTP eşitliği (`timedatectl`). Eşitsiz saat ya da `timedatectl` yoksa WARN. Kimlik daha önce `qradar_clock` idi; ölçtüğü QRadar'ın saati değil, ana makinenin saatidir. Kılavuz kimliği değişmedi. | OPS-R15 |
| `selinux` | `getenforce`. `Enforcing` ise PASS (`SELinux is Enforcing`). `Permissive` ise WARN (`SELinux is permissive: denials are logged, not enforced`). Komut yoksa, `Disabled` ya da boşsa PASS (`SELinux is not in use on this host`). Başka bir kip WARN olur ve kipin adını yazar. | yok |
| `backup_age` | En yeni `qradar-dash-*` yedeği 26 saatten yeni ve yedek zamanlayıcısı etkinse PASS. Yedek yoksa ya da en yenisi 26 saatten eskiyse WARN ve `./qradar-dash backup manual` der. Zamanlayıcı `systemctl is-enabled qradar-dash-backup.timer` ile sorulur (kullanıcı birimi için `--user`); kapalıysa WARN olur ve `systemctl enable --now qradar-dash-backup.timer` der, birim hiç yoksa `./qradar-dash backup --install-timer` der. `systemctl` yoksa yalnız birim dosyasına bakılır (`/etc/systemd/system/qradar-dash-backup.timer` ya da `$HOME/.config/systemd/user/qradar-dash-backup.timer`). İki olgu aynı satırda yazılabilir. | yok |
| `containers` | Compose'un döndürdüğü her servis çalışıyor ve sağlıklı mı. Kapalı `mcp` profilindeki `qradar-mcp` ve 0 koduyla çıkmış `migrate` yok sayılır. | OPS-R36, OPS-R41, OPS-R9, OPS-R11 |
| `doctor_incontainer` | Yalnız backend çalışmıyorsa yazılır. | OPS-R36 |

**Konteyner içi kontroller**

| Kimlik | Ne yapar | OPS-R |
|---|---|---|
| `db_connect` | `SELECT 1`. Hazırlık kontrolüyle aynı olgu. | OPS-R36 |
| `migration_head` | `alembic_version` tablosu imajın başıyla aynı mı. | OPS-R11 |
| `timescale` | Eklenti yüklü ve `log_source_metric` bir hypertable mı. | OPS-R14 |
| `retention_policy` | `RETENTION_ENABLED` true iken gün sayısı ayarlı dört tablonun her birinde saklama politikası var mı; eksik tabloları adlarıyla tek satırda bildirir. Bayrak false iken sorgu atmaz ve "retention is disabled by configuration" yazar. | OPS-R14 |
| `batch_retention` | Günlük satır silicileri, tablolar gün sayısına göre gruplanmış. Kendi kapısı olanlar bir kez yazılır (`WAF_RETENTION_ENABLED`, `IPS_RETENTION_ENABLED`). `RETENTION_ENABLED` bu görevleri kapatmaz. Görev adları bu bölümde ve `docs/CONFIGURATION.md` içindedir. Satır 1000 karakterde kesilir. Son çalışma kaydı yoktur; satır uydurmaz, Redis okumaz. Sorgu atmaz. PASS. | OPS-R14 |
| `redis` | Broker URL'sinde `PING`. | OPS-R9 |
| `celery_workers` | Backend konteynerinin içinden `inspect ping`. En az bir yanıt gerekir. | OPS-R41 |
| `celery_beat` | Redis'teki kalp atışı anahtarı; yaşı 180 sn altında olmalı. | OPS-R9 |
| `qradar_tls` | Yapılandırılmış CA ile el sıkışma. Sertifika bitişi 30 günden yakınsa ya da `QRADAR_TLS_ALLOW_MISSING_AKI` açıksa WARN. | OPS-R2, OPS-R3, OPS-R4, OPS-R18 |
| `qradar_auth` | `GET /system/about`. | OPS-R1 |
| `watermarks` | Her `collection_watermark` satırı: `consecutive_failures` 0 ve gecikme kadansın iki katından az ise PASS. `consecutive_failures` 3 ve üstü ya da `last_error` doluysa FAIL. | OPS-R30 |
| `cadence` | Her `search_cadence` ailesi. Son çalışma yaşı kadans + yerleşme + 60 sn içinde olmalı. | OPS-R30 |
| `families_off` | Kimlik aynı kalır. Satır önce `NAME=on` ya da `NAME=off` listesini, sonra açık aileleri (`on:`) ve kapalı aileleri (`off:`) ayrı yazar. Phase A her zaman açıktır. Yeni kurulum kırmızı görünmesin diye PASS'tir. | OPS-R7 |
| `errors_recent` | `consecutive_failures > 0` olan filigran satırı sayısı: sıfır PASS, bir ya da iki WARN, üç ve üstü FAIL. | OPS-R30 |
| `anomaly_evaluation` | Son 24 saatte verdict üretmeden bırakılan kova varsa FAIL; açık bir fault varsa ya da beklenen yargılama zamanı (kova sonu + yerleşme + bir beat + iki değerlendirme tick'i) geçmiş canlı bir kovası olan izlenen kaynak varsa WARN. | OPS-R61 |
| `pg_database_size` | Veritabanı boyutu. Boş alan ana makine kontrolündedir. | OPS-R14 |
| `encryption` | Saklı bir örnek token'ı çözmeyi dener. Şifreli metin yazdırılmaz. | OPS-R16 |
| `secrets_files` | Token ve CA dosyaları var, boş değil ve uid 10001 tarafından okunabilir. | OPS-R1 |
| `public_origin` | Çalışan ayarlardaki köken. `https` olmalı. | OPS-R12 |
| `version_match` | Frontend probu ile `/api/v1/version` aynı mı. | OPS-R11 |
| `unknown_settings` | Ortamda bir ayara benzeyen ama ayar olmayan adlar. Adlar yazılır, değerler yazılmaz. Böyle bir ad yoksa PASS. Kurulumun yazdığı adlar burada görünmez. | OPS-R62 |
| `ariel_permits` | Global havuz held/cap, ayrılmış canlı yuvalar. Konsol tarafı, tek konsol yokken `console-pool locks N (cap P per console)` olarak yazılır. N bu veritabanındaki bütün konsol kilitleridir, bir konsolun kendi tutuşu değildir. Bütçede gerçekten tek konsol varsa satır `console held/cap` yazar. Havuz doluysa (`held >= cap`) WARN; canlı iş bekler, geçmiş okuma yerini verir. Zorlama kapalıysa `not enforced` ve PASS. Okuma hatasında yalnız hata sınıfı. | OPS-R63 |
| `rule_match_coverage` | `RULE_HEALTH_WINDOW_DAYS` içindeki `FAILED` ve `TRUNCATED` kural-eşleşme pencerelerini tek sorguda sayar; en eski pencereyi, sağlık aralığından çıkacağı UTC zamanı, onarılabilir ve catch-up ufku nedeniyle atlanmış sayıları yazar. Biri varsa WARN, yoksa PASS, toplama kapalıysa SKIP. | OPS-R65 |

`families_off` WARN değil PASS verir. Satır açık ve kapalı aileleri ayrı yazar. Yeni kurulumda
Phase A, catch-all, geo ve explanations açıktır; WAF, IPS, kural eşleşmeleri, özel kurallar ve mülk
izleme bilerek kapalıdır. Arayüz hiç çalışmamış bir aile için "zamanında" değil, tire gösterir.

`celery_beat` kalp atışına bakar (`doctor`: Redis anahtarı, sağlıklı ise 180 s altı).
Bu anahtarı worker yazar; beat yazmaz. Beat'in Compose sağlık kontrolü
`/tmp/celerybeat-schedule` rafının en yeni mtime değerini ölçer (SQLite WAL
kullanıldığında `-wal`, başka bir dbm kullanıldığında `.db` / `.dir` / `.pag`).
Eşik 960 s'dir: Celery `Scheduler.sync_every` 180 s ile bir tick'in uyuyabileceği
`max_interval` 300 s toplanır ve bu aralığın iki katı alınır. Dosya bundan
eskiyse Compose servisi sağlıksız işaretler. Docker sağlıksız bir konteyneri
yeniden başlatmaz; `restart:` yalnız süreç çıkınca uygulanır. `./qradar-dash status`
(`docker compose ps`) ve `doctor` içindeki `containers` satırı bu durumu gösterir.
`celery_beat` satırı dosya yaşı değildir.

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

`logs`, `docker compose logs` komutunun kendisidir: `cmd_logs` aldığı argümanları olduğu gibi iletir
ve kendisi `--tail` ya da `-f` eklemez. Kuyruk ve izleme isteyen operatör onları verir:

```bash
./qradar-dash logs --tail=200 -f backend
```

Servis verilmezse Compose bütün servislerin günlüğünü yazar. Uygulamanın günlük filtresi UUID
biçimli token'ları yalnız zaten bir sır sözcüğü geçen satırlarda gizler. `logs` ikinci bir
temizleyici değildir; temizleyici destek paketidir.

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
Backend imajı yoksa ya da temizleyici hata verirse son arşiv oluşmaz. Bilinmeyen bir argüman
kullanımı yazdırır ve 2 ile çıkar; hiçbir arşiv yazılmaz.

İçeriği: `doctor.txt` (birleşik rapor), `version.txt`, `compose-ps.txt`, `watermarks.txt` ve
`cadence.txt`, `config.env` (sır satırları **maskelenmez, çıkarılır**) ve `logs/` (her servis için
`docker compose logs --tail=2000 --no-color`, temizlendikten sonra).

İçermedikleri: `.secrets/`, iki sır volume'u, `database.dump`, `env.bak`, `secrets.tar`, GHCR
token'ı, oturum çerezleri, parola özetleri.

Paket oluşturulurken `config.env` dosyasından `SECRET_KEY`, `ENCRYPTION_KEY`,
`ENCRYPTION_KEYS_JSON`, `POSTGRES_PASSWORD`, `QRADAR_SEC_TOKEN`, `OIDC_CLIENT_SECRET`, `LLM_API_KEY`,
`SMTP_PASSWORD` ve adında `WEBHOOK`, `PASSWORD` ya da `TOKEN` geçen her anahtar satırı çıkarılır.
Sırlar destek paketine girmez. Paketi bir bilete eklemeden önce yine de elle gözden geçir.

### `repair-state`

```bash
./qradar-dash repair-state
```

Yükseltme ya da geri alma bittikten sonra yığın sağlıklıysa ama `state.json` yazılamadıysa kayıt
düzeltilir. Komut, son kayıt `failed` ise (ya da yükseltmenin geri dönüşü olmayan adımdan önce
yazdığı başarısız deneme duruyorsa), `/api/v1/health/ready` HTTP 200 dönüyorsa ve veritabanındaki
Alembic revizyonu çalışan imajın `migration_head` değeriyle aynıysa yazar. Çalışan sürüm
`last_upgrade.to` ise sonuç `success` olur ve denemenin önceki imajları durur. Çalışan sürüm
`last_upgrade.from` ise (ve ikisi farklıysa) sonuç `rolled_back` olur ve `previous_images` null
yazılır. Son kayıt `success` olsa bile çalışan sürüm o kaydın `from` sürümüyse komut bunu elle
yapılmış bir geri alma olarak kabul eder: ara `failed` yazımı kaybolmuş olsa da site onarılabilir
kalır. Başarıda stdout tam olarak şunu söyler: `The upgrade itself succeeded; do not roll back.`
Geri almada: `The rollback itself succeeded; do not roll back again.`

Reddeder ve hiçbir şey yazmaz: kayıt yoksa, sonuç `rolled_back` ise, `success` kaydında çalışan
sürüm `to` ise, `restored_from` doluysa, sağlık 200 değilse, revizyonlar uyuşmuyorsa, çalışan sürüm
iki uçtan biri değilse. Her ret nedeni, gördüğü değeri ve sonraki komutu adlandırır
(`./qradar-dash status`, `./qradar-dash doctor`, `./qradar-dash logs backend`). Bilinmeyen argüman
2 ile çıkar. Ret ve yazılamama 1 ile çıkar. Kayıt yazıldıysa 0 ile çıkar.

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

## Geçmiş okuma

Platform, kurulumda bir kez sorulan bir soruyla QRadar'ın sakladığı geçmişi okuyabilir: "geçmiş
okunsun mu?". Okunan kovalar **yalnız öğrenme** içindir. Hiçbir olay, ihlal ya da uyarı, bir
kapsamın devralma anından önceki bir aralığa işaret etmez.

### Anahtarlar

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `HISTORY_BACKFILL_ENABLED` | `true` | Konsolun geçmişi okumasını açıp kapatan anahtar. Kapalıyken hiçbir geçmiş adımı çalışmaz; okuyan bir kapsam `PARTIAL` olur, gerekçesi "history reading was switched off" |
| `PW_BOOTSTRAP_ENABLED` | `true` | Mülk izlemelerinin kendi geçmiş okumasının anahtarı. Kapalıyken izleme canlı başlar ve bunu söyler |

Diğer bütün ayarlar, varsayılanları ve sınırları `docs/CONFIGURATION.md` dosyasının "History
backfill" ve "Property-watch history bootstrap" bölümlerindedir. Her kapsam kendi penceresini 72
saatlik bütçeye göre kısar: büyük bir ortam daha az gün okur, kapsamın gerekçesi "window reduced
from N to D days" der. Yedi günden azına inen bir kapsam `UNAVAILABLE` olur ve **hiç arama
yapılmaz**. Kaynak hacmi, catch-all ve kural eşleşmeleri konsol düzeyinde tek pencereye sığar;
catch-all kendi penceresini ilk adımından sonra, o adımın gördüğü yoğunluktan hesaplar. Bir WAF ya
da IPS kaynağı kendi penceresini kendi biçimine göre sığdırır: WAF kaynağı izlediği endpoint
sayısıyla ve bir kaynağın tick'te başlatabileceği arama sayısıyla, IPS kaynağı kabul ettiği varlık
sayısıyla ve tick'in kaynaklar arasında paylaştığı aramaların adil bir payıyla. Sığmayan bir pencere
kısalır ve kapsam gerekçesini söyler. Henüz endpoint ya da varlığı olmayan bir kaynak bir ile
sığdırılır ve bunu da söyler ("fitted with no tracked endpoint yet; the 72-hour limit still bounds
the reading").

### Karar

Kurulum sihirbazı soruyu ilk kurulumda bir kez sorar ve yanıtı konsol adına, ilk toplamadan önce
kaydeder. Yanıt dosyasındaki anahtar `HISTORY_BACKFILL` olup `yes` ya da `no` değerini alır; dosyada
yoksa konsol kararsız kalır ve **ilk yönetici oturum açmasında** soru sorulur. Karar bir yönde
bir kez yazılır: kayıtlı bir kararı kuran değiştirmez. Yönetim arayüzünde bir `No` sonradan `Yes`
olabilir; bir `Yes` asla geri alınamaz, okuma durdurulabilir.

Cevaplar:

- **Yes** — her aile, kendi ilk canlı verisinden önceki günleri kendi toplama adımında okur: kaynak
  hacmi, catch-all gönderen adları ve kural eşleşmeleri her zaman; bir WAF ya da IPS kaynağı
  yalnızca henüz canlı veri toplamamışsa.
- **No** — QRadar'ın geçmişi hiç okunmaz.

### Yönetim sayfası

Sayfa `/admin/history-backfill` yolundadır. Her konsol için iki bölüm vardır: **karar bloğu** (yanıt,
kim, ne zaman) ve **tablo** (aile, kapsam, durum, istenen pencere, kanıtlanan taban, okunan gün ya
da kova, arama sayısı, son adım, gerekçe, "sonradan Yes mümkün mü"). Okunmayan bir sayı `null`
gösterilir, sıfır değil; hiç ölçülmemiş bir sayaç asla sıfır okunmaz.

Kapsamın durumu:

| Durum | Anlamı | Ne yapılır |
|---|---|---|
| `WAITING` | Kapsam kendi ilk canlı kovasını bekliyor | Bekle. Konsolun hiç canlı verisi yoksa tek tick bekler |
| `READING` | Okuma sürüyor; sayılan gün ve kova artıyor | İzle. `history_backfill_max_hours` (varsayılan 72 saat) aşılırsa okuma `PARTIAL` biter |
| `COMPLETE` | İstenen pencere tam okundu, boşluk yok | Başka iş yok |
| `PARTIAL` | QRadar istenenden az tutuyor, ya da bir boşluk var, ya da süre, bütçe ya da anahtar durdurdu | Gerekçeyi oku. Gerekçe sayı verir, kaynak adı vermez |
| `UNAVAILABLE` | Pencerede hiç olay yok, taban taraması yapılamadı ya da en kısa pencerede bile tahmin süre sınırını aşıyor | Bu bir hata değil. O kapsam için geçmiş yok |
| `NOT_ELIGIBLE` | Bu kaynak zaten canlı veri toplamış; geçmişi onun önüne konulamaz | Profili kapat ve yeniden oluştur; sonra okur |

Sayfa "Read history now" ve "Stop reading" düğmelerini sunar:

- **Read history now** — her aileye bir sonraki toplamasında geçmişi yeniden okumasını söyler.
  Konsol düzeyinde okuma bitmişse kapsam yeniden başlar ve yalnız konsol düzeyindeki aileler
  (kaynak hacmi, catch-all, kural eşleşmeleri) yeniden okur; WAF ve IPS için yalnız henüz toplamamış
  kaynaklar okur. Hiçbir şey anında okunmaz, hiçbir eski olay uyarı açmaz. Yeniden başlayan kapsam,
  kendi ilk adımı kendi penceresini yazana kadar eski okumanın kanıtını korur: `days_target`,
  `target_start`, kanıtlanan taban ve okunan aralık eski değerlerdir. Bu arada satır "Reading"
  durumunda, gerekçesi "read history now: topping up" olarak görünür ve hiçbir sayaç görünmez: yeni
  okuma henüz bir şey aramadı, ölçülmemiş sayı sıfır değil `null` olur.
- **Stop reading** — okuyan her kapsamı `PARTIAL` bitirir, gerekçesi "stopped by <kullanıcı>" olur.
  Okunan veriler kalır ve öğrenmede kullanılır; hiçbir şey silinmez. "Read history now" yeniden
  başlatır.

İki düğme de kapsam satırını anında değiştirmez; istek karar satırına yazılır ve her aile kendi
bir sonraki toplamasında uygular.

### Takılı bir okumanın belirtileri

- `READING` durumu `history_backfill_max_hours` (varsayılan 72 saat) boyunca değişmiyorsa okuma
  zaman sınırına dayanmıştır; kendiliğinden `PARTIAL` olur ve gerekçesi "the reading did not finish
  in 72 h" der.
- Okunan gün sayısı ilerlemiyorsa canlı geçiş tick'in arama bütçesini tüketmiştir: geçmiş
  adımı, canlı geçişin bıraktığı boşluğu harcar, kendi bütçesi yetmezse hiç arama yapmaz.
- Gerekçede art arda "boşluk" varsa sağlayıcı aramaları başarısız oluyordur; `./qradar-dash logs`
  ile bak.
- Kapsam hiç `READING` olmuyorsa ailenin kendi toplama anahtarı kapalıdır ya da konsolun `No`
  yanıtı vardır.

Çözüm sırası: ailenin kadansını `/admin/search-cadence` sayfasından doğrula, QRadar erişimini ve
token'ı denetle, gerekirse `HISTORY_BACKFILL_SEARCHES_PER_TICK` ile `HISTORY_BACKFILL_SECONDS_PER_TICK`
değerlerini büyüt. Hiçbiri işe yaramazsa "Stop reading" ile durdur: okunan veriler kalır, okuma
`PARTIAL` biter.

### Komut satırı

Kurulum bu yanıtı şu komutla kaydeder:

```bash
docker compose run --rm --no-deps -T backend \
  python -m app.cli.history_backfill decide \
    --instance <konsol-adı> --answer yes --source installer
```

| Çıkış kodu | Anlamı |
|---:|---|
| `0` | Yanıt kaydedildi ya da bir karar zaten vardı ve olduğu gibi bırakıldı |
| `1` | Veritabanı başarısız; hiçbir şey kaydedilmedi |
| `2` | Konsol kayıtlı değil ya da argümanlar geçersiz; hiçbir şey kaydedilmedi |

Komut kayıtlı bir kararı asla değiştirmez ve QRadar'a arama yapmaz.

### Sınırlar

- **Okunan geçmiş hiç yargılanmaz.** Okunan bir kovanın aralığı, hiçbir olayın, ihlalin ya da
  uyarının konusu olamaz; yalnız taban çizgisi ve öğrenme için kanıttır. Grafikler bu kovaları
  metinle etiketler ("read from QRadar history, not judged").
- **WAF ve IPS yalnız yanıttan sonra ilk kez toplanan kaynaklar için geçmiş okur.** Zaten canlı
  kovası olan bir kaynak `NOT_ELIGIBLE` olur: geçmiş onun önüne konulamaz. Profili kapatıp yeniden
  oluşturmak okumasını sağlar.
- **Faz A'nın çapasından sonra eklenen ya da izlemeye alınan bir kaynağın geçmişi yoktur.** Okuma
  konsolun en eski canlı kovasında biter; sonradan eklenen bir kaynağın o noktadan önce olayı
  yoktur. Böyle bir kaynak "sessiz" sayılır ve okuma `PARTIAL` biter, gerekçesi "N sources had no
  event in the window" olur. İzlemeye alınmamış bir kaynağa ilk okumada geçmiş satırı yazılmaz;
  "Read history now" ile yeniden okuma, artık izlenen kaynaklar için satır yazabilir.
- **24 saatlik bazı sayılar geçmiş kovalarını da sayar.** Catch-all gönderenlerinin `events_24h`
  değeri ve WAF durumunun son 24 saatteki kesik ve alt sınır kova sayıları, geçmişten okunan
  kovaları da kapsar; bu sayılar yalnız gösterim içindir, hiçbir kararı beslemez.
- **Pencere küçülürse yazılmış satırlar kalır.** Bir adımda pencere daralırsa daha önce yazılmış
  satırlar silinmez; tabloda kapsamın `days_target` değerinden daha fazla gün olabilir. Yazılan
  satırlar gerçek ölçümlerdir.
- **`0030` migrasyonunun downgrade'u geri alınamaz.** İndirme, geçmişten okunmuş satırlarla eğitilmiş
  taban çizgilerini bırakır. Alembic downgrade hiçbir zaman kullanılmaz (bkz. OPS-R34).
- **Üretim konsolunda geçmişe güvenmeden önce konsolun sahibinden saklama kovalarını doğrula.**
  Laboratuvar konsolunda saklama kovaları okunamadı; hangi kovaların hangi süreyle tutulduğu ve
  silmenin kova içinde en eskiden yeniye mi yapıldığı konsoldan doğrulanmalıdır. Bu doğrulanmadan
  geçmiş okumaya dayalı bir taban çizgisine güvenme.
- **Havuz meşgulken geçmiş okuma yerini verir.** Canlı toplama ayrılmış yuvaları kullanır. Bir
  geçmiş adımı izin alamazsa kapsam ilerlemez: `through` yerinde kalır, durum değişmez, önceki
  `last_error` sınıfı ve zamanı durur. Gerekçe `deferred: no Ariel permit within N s` olur. N,
  beklenen saniyedir. Bu bir hata değildir; havuz boşalınca aynı adım yeniden denenir.

### Mülk izlemesinin kendi geçmişi

Konsol düzeyindeki okumadan ayrı olarak, tek bir mülk izlemesi kendi geçmişini isteyebilir.
Böyle bir okuma konsolun "geçmiş okunsun mu" kararından bağımsızdır: kendi anahtarı, kendi
penceresi ve kendi gerekçesi vardır. `/admin/history-backfill` sayfasında görünmez, izleme
sayfasında yaşar. Okunan kovalar yine de yalnız öğrenme içindir: hiçbir olay, ihlal ya da uyarı
bu aralığa işaret etmez.

**Anahtar ve kapalıyken ne olur.** `PW_BOOTSTRAP_ENABLED` (varsayılan **true**). Kapalıyken:

- Yeni bir izleme `history_days` verilmeden oluşturulursa geçmiş istemez. Verilen değer yine de
  saklanır, toplayıcı bunu bildirir.
- Geçmiş isteyen ama henüz okumamış bir izleme kendi bir sonraki tick'inde `PARTIAL` olur,
  gerekçesi "history reading is switched off". O tick'ten itibaren yalnız canlı toplar; geçmişten
  hiçbir kova yazılmaz.
- Okuyan bir izleme kendi bir sonraki tick'inde `PARTIAL` olur, gerekçesi "history reading was
  switched off". Okunmuş kovalar kalır.
- Düzenleyicinin "5. History" bölümü "History reading is switched off on this server" notuyla
  kapanır ve sayfa "Read history" düğmesini göstermez.
- Tick bütçesinin 409'u uygulanmaz. Yine de bir geçmiş isteği kabul edilir ve bir sonraki
  tick'te `PARTIAL` ile kapanır.

**İstek.** İstenen gün sayısı 0 ya da `PW_NEW_VALUE_HISTORY_DAYS` (7) ile `PW_BOOTSTRAP_DAYS`
(28) arasında olmalı; başka bir değer 422 alır. Anahtar açıkken yeni bir izleme `PW_BOOTSTRAP_DAYS`
gün ister. Bir izlemenin geçmişi yalnız bir kez istenebilir: okuma başlamış ya da bitmiş bir
izlemede `history_days` değiştirmek 409 alır ("history is COMPLETE on this watch and cannot be
requested again; create a new watch to read history"). Aynı 409, izleme zaten kova toplamışsa ya da
kendi sürümünde kova tutuyorsa gelir. İstek yalnız kaydeder; okumayı bir sonraki tick'teki
toplayıcı başlatır ve gerekçesi "history was requested, and the scope can be planned at its next
collection" olur.

**Okuma durumları.**

| Durum | Anlamı | Ne yapılır |
|---|---|---|
| `NOT_REQUESTED` | Bu izleme geçmiş istemedi | — |
| `READING` | Okuma sürüyor | İzle |
| `COMPLETE` | İstenen pencere tam okundu, boşluk yok | Başka iş yok |
| `PARTIAL` | Pencere tam okunmadı ya da bir boşluk var | Gerekçeyi oku |
| `UNAVAILABLE` | QRadar bu kapsamda hiç olay tutmuyor | Bu bir hata değil; izleme ileriye doğru öğrenir |

`UNAVAILABLE` üç durumda çıkar: pencerede hiç olay yok, ilk olay kanıtlanan tabandan daha yeni
(taban payı `PW_BOOTSTRAP_FLOOR_MARGIN_HOURS`, varsayılan 24 saat), ya da taban taraması
`PW_BOOTSTRAP_MAX_FAILURES` kez başarısız oldu.

**Ne zaman yargılanır.** Okunan geçmiş, bir hücrenin güvenilir olması için gereken ziyaret sayısını
besler: bir hücre (haftanın günü ve saati) en az `PW_BASELINE_MIN_VISITS` (varsayılan 2) **ayrı hafta
ziyareti** ve en az `BASELINE_MIN_SAMPLES` (8) temiz örnek ister. Bir değer ancak `7 x
PW_BASELINE_MIN_VISITS` günlük geçmişten sonra devralma anında hacim yargısı verebilir: varsayılanla
14 gün, 28 günlük varsayılan pencere dört ziyaret verir. Daha kısa bir geçmişte sıçrama, düşüş ve
kaybolma dedektörleri `INSUFFICIENT_DATA` verir ("... they come from too few separate weekly visits
of this weekday and hour: a baseline needs the same hour from at least 2 different weeks") ve canlı
kovalar ziyaretleri tamamlayıncaya kadar öyle kalır. Yeni değer ve yeni bileşim dedektörleri bu
kapıdan etkilenmez: onlar geçmişin kendisini kanıt olarak kullanır.

Bir izlemenin `WAITING` ya da `NOT_ELIGIBLE` durumu yoktur: izleme hareketli bir sonla okur,
çapası gerekmez. Okuma sürerken izlemenin durumu `READING_HISTORY` olur (rozet "Reading history")
ve durumun gerekçesi ilerlemeyi söyler: "Reading history from QRadar: 3.2 of 28.0 days; nothing is
judged until it finishes". Devralma anında bu durum temizlenir.

**Analist nerede görür.**

- İzleme sayfası `/behavior/watches/<id>`: "History" bölümü. Durum, istenen gün sayısı, istenen
  pencere, QRadar'dan okunan aralık, kanıtlanan taban ve boşluklar burada listelenir.
- Sayfanın başlığı: durum rozeti ve "3.2 of 28.0 days read. Nothing is judged until it finishes"
  satırı.
- Dürüstlük paneli: "READING HISTORY" notu, her boşluk için bir "HISTORY GAP" notu ve saklanan
  ilerleme okunamadıysa "HISTORY PROGRESS UNREADABLE" notu ("This is not zero days read").
- API: her izleme okumasında bir `history` bloğu gelir: durum, istenen gün, pencerenin başı,
  kanıtlanan taban, okunan aralık, okunan gün, arama sayısı, okunan kova sayısı, boşluklar,
  gerekçe, başlangıç ve bitiş saati, bitmemiş bir istekte tahmin. Ölçülmemiş bir sayı `null`
  olur, sıfır değil. Tahmin bir vaat değildir, planlayıcının aritmetiğidir; süre garantisi yoktur.

**Takılı bir okumanın belirtileri.**

- **Zaman sınırı.** Okuma `PW_BOOTSTRAP_MAX_HOURS` (varsayılan 72 saat) boyunca sürerse kendiliğinden
  `PARTIAL` olur, gerekçesi "the reading did not finish in 72 h". Okuma bitmiştir, izleme canlı
  toplamaya devam eder. Yeniden okumak isteyen operatör yeni bir izleme kurar.
- **En dar genişlikteki başarısızlıklar.** Bir arama başarısız olunca genişlik yarıya iner. En dar
  genişlik 12 kovadır (5 dakikalık kovalarda bir saat). En dar genişlikte üst üste
  `PW_BOOTSTRAP_MAX_FAILURES` (varsayılan 3) başarısızlık olunca o aralık bir boşluk olur:
  "search failed 3 times at the minimum width"; daha önce her denemede "a search failed at the
  minimum width (1 of 3); the same span is tried again" yazılır. Boşluklar 20'yi aşarsa okuma
  `PARTIAL` olur: "more than 20 unreadable spans". Gerekçe bir aramanın başarısız olduğunu
  söyler: QRadar erişimini ve token'ı denetle (OPS-R1 ... OPS-R4), `./qradar-dash logs` ile arama
  hatalarına bak. Boşluklar aralıklarıyla listelenir; okunan diğer kovalar geçerlidir.
- **Yoğunluk kapısı.** Okunan kovaların alt sınır olanlarının payı
  `PW_BOOTSTRAP_MAX_LOWER_BOUND_SHARE` (varsayılan %5) sınırını aşarsa okuma `PARTIAL` olur: "12%
  of the buckets read are lower bounds (limit 5%): too many distinct values per bucket, so history
  would be lower bounds". Alt sınır, tek kovada satır tavanına çarpmış bir aramanın sonucudur:
  saklanan satırlar tamdır, kuyruğu eksiktir; sayı bir alt sınırdır, sıfır değil. Bu bir arıza
  değildir: `PW_BOOTSTRAP_MAX_LOWER_BOUND_SHARE` (0.0 - 1.0) ile kapı gevşetilebilir ya da daha dar
  kapsamlı yeni bir izleme kurulabilir.
- **Tick'in arama bütçesi.** Canlı toplama tick'in bütçesini önce harcar, geçmiş adımı kalanı
  alır. Boş kalan arama sayısı bir adımın maliyetinden azsa adım hiç arama yapmaz, gerekçesi
  "waiting for search budget" olur. Etkin izleme sayısını azalt ya da `PW_MAX_SEARCHES_PER_TICK`
  (40), `PW_BOOTSTRAP_SEARCHES_PER_TICK` (12) ya da `PW_BOOTSTRAP_SECONDS_PER_TICK` (180)
  değerlerini büyüt.
- **Bütçe ayarı bir adımın maliyetinden küçük.** `PW_BOOTSTRAP_SEARCHES_PER_TICK` bir adımın
  maliyetinden küçükse okuma hiç arama yapamaz ve gerekçesi bunu söyler: "PW_BOOTSTRAP_SEARCHES_PER_TICK
  is 2; reading history for this watch needs at least 3 searches per tick (TOTALS, PINNED and one
  GROUPED)". Bir adım sabitlenmiş (pinned) değer varsa üç arama (TOTALS, PINNED, bir GROUPED),
  yoksa iki arama tutar. Ayarı 3 ya da daha büyük bir değere çıkar.
- **Bütçe 409'u.** İstek daha ilk adımda reddedilebilir. Bir adım sabitlenmiş (pinned) değer varsa
  üç arama (TOTALS, PINNED, bir GROUPED), yoksa iki arama tutar; canlı toplamanın payı
  düşüldükten sonra kalan arama sayısı bundan azsa istek 409 alır: "reading history needs at
  least 3 free searches per tick (TOTALS, PINNED and one GROUPED), but 1 are left after the enabled
  watches' live cost of 39 of 40". Ayarın kendisi bu maliyetin altındaysa gerekçe başka olur:
  "PW_BOOTSTRAP_SEARCHES_PER_TICK is 2; reading history for this watch needs at least 3 searches
  per tick (TOTALS, PINNED and one GROUPED)". 409'un gövdesi `codes` de taşır: `HISTORY_BUDGET`.
  Bir izlemeyi devre dışı bırak ya da `PW_MAX_SEARCHES_PER_TICK` değerini büyüt; olmuyorsa izleme
  sayfasındaki "Enable without history" düğmesi izlemeyi geçmişsiz etkinleştirir: izleme yalnız
  ileriye doğru öğrenir, QRadar'ın geçmişi okunmaz. Bu düğme yalnızca reddedilen tek sebep geçmiş
  bütçesiyse görünür.
- **Okunmayan ilerleme.** Saklanan ilerleme geri okunamazsa okuma `PARTIAL` olur, gerekçesi "the
  stored history progress could not be read back". Sayılar `null` olur, sıfır değil. Arayüzden
  onarılacak bir şey yoktur: okuma kapanmıştır, izleme canlı toplamaya devam eder.

---

## Kapsam eşlemeleri

`/coverage` sayfası "No ATT&CK technique mappings are configured, so coverage cannot be measured"
yazıyorsa hiçbir eşleme yoktur. Platform MITRE içeriği almaz: her eşleme SOC'un kendi kararıdır ve
API ile elle girilir. Eşleme bir ATT&CK teknik kimliğini (örn. `T1110`) platformun bir analiz
kuralına bağlar. Yalnız platformun kendi veritabanına yazılır; QRadar'a hiçbir şey yazılmaz.

**İzin.** `POST /api/v1/coverage/mappings` için `coverage:write` gerekir; izin her aramadan önce
denetlenir. Yerleşik rollerden `admin` (`admin:*` ile), `analyst` ve `detection_engineer` bu izne
sahiptir; `viewer` yalnız `read:*` taşır ve 403 `missing required permission: coverage:write` alır.

**Kuralın UUID'sini bul.** `rule_id` platformun kural kimliğidir (UUID), QRadar'ın sayısal kural
kimliği (`qradar_id`) **değildir**. İki yol vardır:

- `/rules` sayfasında kuralın adına tıkla; adres `/rules/<UUID>` olur.
- API: `GET /api/v1/rules?search=<kural adı>` yanıtındaki `items[].id` alanı (`read:rules` gerekir;
  `qradar_id` aynı kayıttadır, karıştırma). Yapı taşları listede yoktur;
  `include_building_blocks=true` ekle.

**Eşleme ekle.** Oturum çerezi `qd_session`'dır. Komut satırından oturum açarken isteğe siteyle
eşleşen `Origin` başlığını ekle (`https://<ad>`); bu araçta ayrı bir CSRF belirteci başlığı yoktur,
koruma `Origin` denetimidir (bkz. OPS-R22). İlk oturum açmada parola değiştirilmiş olmalıdır
(bkz. OPS-R27). Parola kabuk geçmişine girmesin diye klavyeden okunur (içinde `"` ya da `\` olan
bir parola JSON'a böyle konamaz):

```bash
read -rs QD_PAROLA
printf '{"username":"<kullanıcı>","password":"%s"}' "$QD_PAROLA" |
  curl -sS -c cerez.txt -H 'Origin: https://<ad>' -H 'Content-Type: application/json' \
    --cacert .secrets/tls.crt --data @- https://<ad>/api/v1/auth/login
unset QD_PAROLA

curl -sS -b cerez.txt -H 'Origin: https://<ad>' -H 'Content-Type: application/json' \
  --cacert .secrets/tls.crt -X POST https://<ad>/api/v1/coverage/mappings \
  --data '{"technique_id":"T1110","technique_name":"Brute Force","tactic":"Credential Access",
           "rule_id":"<kuralın-UUID-si>","confidence":1.0,"provenance":"<kısa not>"}'
rm cerez.txt
```

`--cacert <dosya>`, `curl`'e o dosyadaki sertifikaları verici olarak güvenilir saymasını söyler.
`.secrets/tls.crt` sitenin kendi sertifikasıdır (kurulumda `TLS_CERT_FILE` olarak verilen dosya).
Sertifika kendinden imzalıysa `--cacert .secrets/tls.crt` doğrudur: sertifika kendi vericisidir.
Sertifikayı bir iç CA verdiyse bu dosya işe yaramaz ve `curl` çıkış 60 ile durur; onun yerine o CA'nın
sertifika dosyasını ver (tarayıcılara güvenilir diye tanıtılan dosya; kurulum onun yolunu kaydetmez).
Bu makine sertifikaya zaten güveniyorsa `--cacert` parametresini çıkar. Her durumda `https://<ad>`
içindeki ad sertifikadaki adla eşleşmelidir. `-k` kullanma; install özetinin "site CA'sı güvenilir
olana kadar" uyarısı da aynı durumu anlatır.

Gövde alanları: `technique_id` (2-32 karakter, zorunlu), `rule_id` (zorunlu), `technique_name`,
`tactic`, `provenance` (isteğe bağlı), `source` (`EXPLICIT` varsayılandır; SOC'un kendi eşlemesi
budur) ve `confidence` (0 ile 1 arası, varsayılan 1.0). Birden çok konsol varsa `?instance_id=<UUID>`
ekle; verilmezse ilk kayıtlı konsol kullanılır.

| Yanıt | Anlamı |
|---|---|
| 201 | Eşleme kaydedildi; gövde kaydı döndürür. |
| 400 `no QRadar instance configured` | Kayıtlı konsol yok; `install` konsolu kaydeder, onu bitir ya da yeniden çalıştır. |
| 403 `missing required permission: coverage:write` | Kullanıcının rolünde izin yok. |
| 404 `rule not found for this instance` | `rule_id` bu konsolda yok; `qradar_id` değil UUID'yi ver ve kural envanterinin eşitlendiğini kontrol et. |
| 409 `mapping already exists` | Aynı teknik ve kural çifti zaten kayıtlı. |
| 422 | Gövde geçersiz (kısa `technique_id`, 0-1 dışı `confidence`). |

Eşleme her eklenişte denetim kaydına `coverage.mapping.create` olarak yazılır.

**Sonucu hesapla.** Kapsam kararlarını `evaluate_detection_coverage` görevi hesaplar; beat onu
`COVERAGE_EVALUATION_INTERVAL_SECONDS` aralığıyla çalıştırır (varsayılan 3600 saniye, en az 60).
Beklemek istemezsen elle çalıştır:

```bash
docker compose exec -T backend python -m app.cli.sync coverage
```

`--instance <konsol-adı>` yalnız o konsolu işler, `--json` makine okunur çıktı verir. Sonuç
`/coverage` sayfasında görünür. Kural sağlığı kapsamdan önce hesaplanır (`sync all` sırayı böyle
tutar); kural envanteri henüz eşitlenmediyse önce `sync rules` ve `sync rule-health` çalıştır.

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

**Çözüm:** `docs/INSTALL.md` içindeki macvlan bölümü ve
`deploy/docker-compose.override.example.yml`. Önce `./deploy/create-vmnet.sh`
(`qradar-vmnet`; `--ip-range` DHCP havuzunun dışında). Örneği git tarafından yok
sayılan `docker-compose.override.yml` olarak kopyala. Örnek ağı yeniden tanımlamaz;
`external: true` ve `name: qradar-vmnet` ile betiğin oluşturduğu ağa bağlanır. Ağ
yalnız QRadar'a bağlantı açan servislerdedir: `backend`, `celery-worker` ve profil
açıksa `qradar-mcp`. `celery-beat` yalnız görev adını brokera yollar; `migrate`
yalnız Postgres'e konuşur. Betik dosya varsa bütün Compose çağrılarına ekler ve
kullanımını yazdırır. Ya da bir rota. Betik bir güvenlik duvarı değişikliği yapmaz.
`:z` ve `label=disable` kullanılmaz.

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

**Olası neden:** SELinux `user_home_t`. Checkout'tan bağlanan bir dosyayı konteyner okuyamaz.
MCP servisi artık checkout'u bağlamaz: `feature_toggles.json` imajın içindedir ve CA ile token
adlandırılmış `qradar_secrets` birimindendir. Bu belirtiyi gören servis büyük olasılıkla
checkout'tan bir dosya bağlayan yerel bir `docker-compose.override.yml` taşıyordur.

**Doğrulama:** `ausearch` ya da günlük, `container_t` için `denied` gösterir ve `getenforce`
çıktısı `Enforcing` olur.

```bash
getenforce
```

**Çözüm:** **Yeniden etiketleme yapma.** SELinux kapatılmaz ve `:z` kullanılmaz. Uygulama
token'ı ve CA için `load-secrets.sh` kullanmaya devam et. Bağlama noktasını override
dosyasından kaldır. `mcp` profili 2026-10-23'te gerçek bir konsola karşı doğrulandı; üretimde
nasıl açıldığı `docs/INSTALL.md` içinde, Optional MCP service bölümündedir. Profil için kopyalanacak
ya da düzenlenecek bir dosya yoktur.

### OPS-R14. Postgres `No space left on device` veriyor ya da `doctor` diski FAIL gösteriyor

**Belirti:** Postgres `No space left on device` hatası veriyor ya da `doctor` disk satırı FAIL
gösteriyor.

**Olası neden:** Timescale politikası yoktu (`RETENTION_ENABLED` false ya da `apply_timescale_policies`
hiç çalışmadı) ya da günlük siliciler beklenen pencereden uzun tutuluyordu. `RETENTION_ENABLED=false`
yalnız dört Timescale parça politikasını kaldırır. Günlük siliciler yine çalışır: arama sonuçları 90
gün (`apply_search_result_retention`), catch-all 35 (`apply_catchall_retention`), coğrafya 35
(`apply_geo_retention`), WAF metrik 35 / kapalı olay 180 (`apply_waf_retention`, kapı
`WAF_RETENTION_ENABLED`), IPS metrik 35 (`apply_ips_retention`, kapı `IPS_RETENTION_ENABLED`) /
kapalı olay 365 (`apply_ips_anomaly_retention`), property-watch metrik 35 / kapalı olay 180
(`apply_property_watch_retention`), anomaly fault 30 (`apply_anomaly_fault_retention`), özel kural
ihlali 180 (`apply_custom_rule_retention`), kapalı Phase A olay 365
(`apply_log_source_anomaly_retention`), `rule_health_snapshot` 90 ve `detection_coverage_snapshot`
90 (`apply_snapshot_retention`), `rule_match_metric` ve `rule_match_window` 90
(`apply_rule_match_retention`). Docker json günlükleri 20 MB × 5 ile sınırlıdır ve diski dolduran
şey bunlar değildir.

**Doğrulama:** `disk_docker` ya da `disk_backup` FAIL ve `pg_database_size` satırı.

```bash
./qradar-dash doctor
```

**Çözüm:** Yer aç. `retention_policy` Timescale politikalarını doğrular (PASS). `batch_retention`
günlük silicilerin gün sayılarını yazar; son çalışmayı kaydetmez, Redis'ten de okumaz. Docker
günlük dosyalarını silmek dolu bir veri volume'unu düzeltmez.

### OPS-R15. Ana makine askıya alındıktan sonra filigranlar atlıyor ya da çok sayıda kaynak `TIMESTAMP_DELAY` gösteriyor

**Belirti:** Ana makine askıya alındıktan sonra filigranlar atlıyor ya da birçok kaynak
`TIMESTAMP_DELAY` gösteriyor.

**Olası neden:** Ana makine saati sıçradı. Beat UTC kullanır. Gecikme dedektörü gerçek bir anomali
türüdür.

**Doğrulama:** `host_clock` WARN ve `timedatectl` eşitli değil. Bu kimlik daha önce `qradar_clock`
idi. Ölçtüğü QRadar konsolunun saati değil, bu ana makinenin NTP eşitliğidir.

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
`ENCRYPTION_KEYS_JSON` ve `ENCRYPTION_KEY_VERSION` mevcut `.env` içinde bulunduğunda yeniden
`install` çalıştırmak ikisini de korur. Hatayı "düzeltmek" için yeni anahtar üretme.

### OPS-R17. Backend `Unsafe configuration for ENVIRONMENT=<ortam>` ile yeniden başlıyor

**Belirti:** Backend `Unsafe configuration for ENVIRONMENT=staging` ya da
`ENVIRONMENT=production` hatasıyla yeniden başlıyor. Satır ortamın adını taşır.

**Olası neden:** Başlangıç kontrollerinden biri: `DEBUG`, kapalı TLS, kısa `SECRET_KEY`, eksik
`ENCRYPTION_KEY`, `QRADAR_PROVIDER=mock`, token yok, otonom LLM, hatalı `PUBLIC_ORIGIN` ya da
`AUTH_PROVIDER=oidc`. Staging aynı hatayı verir; staging, üretim öncesi sitenin üretim
duruşudur. `.env.example` içindeki yer tutucu `SECRET_KEY` ve `ENCRYPTION_KEY` değerleri ile
Fernet'in kuramadığı bir `ENCRYPTION_KEY` ya da `ENCRYPTION_KEYS_JSON` girdisi de nedendir.

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
boş kalır. Roller AD'den okunmaz (üretim girişi, 2026-10-16: yerel hesaplar ve AD LDAP üzerinden; roller uygulamada atanır).

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

**Çözüm:** `docs/INSTALL.md` içindeki yeniden oluşturma komutu. Bu geçersiz kılmayı hiç oluşturmamış bir üretim
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

**Çözüm:** Postgres sağlığına bak. `/ready`, `alembic_version.version_num` değerini
imajın Alembic başıyla karşılaştırır. Baş uyuşmazlığında 503 her iki revizyonu
adlandırır. Tablo yoksa ya da sorgu hata verirse yanıt yine 503'tür ve veritabanı
hata metnini içermez. `OPS-R11` (`doctor` `migration_head`) ve `OPS-R14` ile doğrula.

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

**Çözüm:** Yeni bir token. Kayıt defterine hiç ulaşılamıyorsa yol çevrimdışı pakettir: bağlı bir makinede
`./qradar-dash bundle`, üretimde `./qradar-dash install --offline BUNDLE` ya da
`./qradar-dash upgrade <sürüm> --offline BUNDLE` (bkz. [Çevrimdışı paket](#çevrimdışı-paket)).

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

### OPS-R46. Hiçbir mülk izlemesi toplanmıyor

**Belirti:** `/behavior/watches` listesi boş ya da her izleme `NOT_ENABLED` gösteriyor; hiçbir izleme
için toplanmış kova yok.

**Olası neden:** `PW_COLLECTION_ENABLED` varsayılanda false'tur; false iken
`dispatch_property_watch_collection` görevi hiç oturum açmaz ve QRadar'a arama gitmez, bu yüzden
ailenin `last_run_at` alanı NULL kalır. Kaydedilen bir izleme her zaman **devre dışı** başlar ve
çalışana kadar durum göstermez (`last_status` boştur); daha önce etkinleştirilip sonra kapatılan
bir izleme `DISABLED` gösterir ve gerekçede onu kimin kapattığını söyler; hiç etkinleştirilmemiş
bir izleme toplanmaz. `PROPERTY_WATCH` aile kadansı
(varsayılan 5 dk) henüz dolmamışsa kova da yoktur.

**Doğrulama:** `doctor` çıktısındaki `cadence` satırı `PROPERTY_WATCH` ailesini sayar ve
`families_off` satırı `PW_COLLECTION_ENABLED` anahtarını gösterir. **Dikkat:** aile kapalıyken
`cadence` satırı `PROPERTY_WATCH`'i listelemez; `cadence` satırında WARN görmek tek başına
toplamanın açık olduğunu göstermez (aile açık ama kadansı henüz dolmamış da olabilir). Kesin
doğrulama izlemenin durumudur:

```bash
./qradar-dash doctor
```

```
GET /api/v1/property-watches/{id}
```

**Çözüm:** İstenirse `PW_COLLECTION_ENABLED` ayarını aç, sonra izlemeyi etkinleştir
(`PATCH /api/v1/property-watches/{id}`); etkinleştirme üst sınır ya da bütçe yetmezse 409 verir
(OPS-R50). Aile kadansını `/admin/search-cadence` sayfasından doğrula. Yeni kurulumda kapalı bir aile
beklenen durumdur (Kurulum, Sihirbaz).

### OPS-R47. İzleme `SCOPE_STALE` durumunda takılıyor

**Belirti:** Bir grup ya da tip izlemesi `SCOPE_STALE` gösteriyor ve kova yazmıyor.

**Olası neden:** Envanter eşitlemesi (inventory sync) grubu ya da tipi yenilemedi. Kapsam, yürünen
**en eski** grup satırının `synced_at` alanı `PW_SCOPE_STALE_AFTER_HOURS` (varsayılan 24) saatten
eski olduğunda bayatlar. Tek bir eşitleme geçişi tüm tip ve grup satırlarını aynı zamanla damgalar;
başarısız bir grup listelemesi yutulur, bu yüzden kısmi bir geçiş satırları eski bırakabilir.

**Doğrulama:** Kapsam okuması durumu, gerekçesini ve envanterin yaşını (saniye, UTC) verir:

```
GET /api/v1/property-watches/{id}/scope
```

`doctor` `watermarks` ve `cadence` satırları eşitleme gecikmesini gösterir.

```bash
./qradar-dash doctor
```

**Çözüm:** Envanter eşitlemesinin çalıştığını ve QRadar'a erişebildiğini doğrula (OPS-R4, OPS-R10).
Eşitleme düzeldiğinde kapsam bir sonraki tick'te yeniden çözülür; kapsam çözülmediği sürece son
eşitlemenin üye sayısı ve adları yine döner, sıfır değil.

### OPS-R48. İzleme `SCOPE_TOO_LARGE` gösteriyor

**Belirti:** Bir grup izlemesi `SCOPE_TOO_LARGE` gösteriyor ve kova yazmıyor; ya da etkinleştirme
kapsamın çok büyük olduğunu söyleyen bir 409 ile reddediliyor.

**Olası neden:** Çözülen grup `PW_MAX_SOURCES_PER_SEARCH` (50) kaynaklık parçalara (chunk) bölünür;
`PW_MAX_CHUNKS_PER_WATCH` (4) parçadan fazlasını gerektiren bir kapsam (yaklaşık 200 kaynak)
bilerek reddedilir, ki tek bir izleme tick bütçesini yakmasın.

**Doğrulama:** Kapsam okumasındaki `member_count`, `groups_walked` ve `SCOPE_TOO_LARGE` durumu.
Editör kataloğu sınırları gösterir:

```
GET /api/v1/property-watches/{id}/scope
```

```
GET /api/v1/property-watches/catalog
```

**Çözüm:** Kapsamı böl: daha küçük bir grup seç, birkaç izlemeye ayır ya da kaynak düzeyinde tek
izlemeler kur. Kapsam yeniden çözülünce kova yazılmaya başlar.

### OPS-R49. İzleme `SCOPE_EMPTY` gösteriyor

**Belirti:** Bir grup ya da tip izlemesi `SCOPE_EMPTY` gösteriyor ve kova yazmıyor.

**Olası neden:** Çözülen kapsam tek kaynak içermiyor: grup kimliği envanterde yok (silinmiş grup) ya
da grubun/tipin hiçbir üyesi yok.

**Doğrulama:** Kapsam okuması `SCOPE_EMPTY` durumunu ve son eşitlemenin üye sayısını verir. Boş bir
grubun ölçülmüş sıfırı `0` olarak, `SCOPE_EMPTY` ile birlikte raporlanır.

```
GET /api/v1/property-watches/{id}/scope
```

**Çözüm:** Kaynağın envanterde gruba/tip'e bağlı olduğunu doğrula ve envanter eşitlemesinin güncel
olduğundan emin ol (OPS-R47); kapsamı mevcut bir gruba ya da kaynağa çevir.

### OPS-R50. Etkinleştirme 409 ile reddediliyor (etkin izleme üst sınırı ya da arama bütçesi)

**Belirti:** `PATCH /api/v1/property-watches/{id}` gövdesinde `enabled=true` iken istek 409
döndürüyor; gerekçe etkin izleme üst sınırını ya da tick bütçesini adlandırıyor.

**Olası neden:** Etkin izleme sayısı `PW_MAX_ENABLED_WATCHES` (10) sınırına dayandı, ya da etkin
izlemelerin statik maliyet toplamı `PW_MAX_SEARCHES_PER_TICK` (40) aramayı aşıyor (örn. iki 12-arama
grup izlemesi artı altı 3-arama izlemesi 42 eder). Etkinleştirme ikisini de önceden reddeder ki
sığmayan bir yapılandırma hiç başlamasın.

**Doğrulama:** Katalog ucu sayıları verir (`max_enabled`, `enabled_count`, `search_budget`,
`budget_used`):

```
GET /api/v1/property-watches/catalog
```

**Çözüm:** Gereksiz bir izlemeyi devre dışı bırak ya da sil, kapsamları küçült (daha az parça = daha
az arama) veya operatör istiyorsa `PW_MAX_ENABLED_WATCHES` / `PW_MAX_SEARCHES_PER_TICK` ayarlarını
yükselt. Sınırlar bilerek ayardır; yükseltmeden önce PW-10 lab ölçümünü bekle.

### OPS-R51. Dedektörler "öğreniyor" durumunda kalıyor

**Belirti:** Bir izlemenin değerleri günlerce `INSUFFICIENT_DATA` gösteriyor; arayüz "learning" der ve
bazı değerler için "baseline: N of 168 hour-of-week cells reliable" satırındaki N düşük kalır.

**Olası neden:** Her dedektörün kendi öğrenme gereksinimi vardır. Taban sapması (`PW_VALUE_SPIKE`,
`PW_VALUE_DROP`) için bir hücrenin **8 temiz örnek** toplaması ve en az `PW_BASELINE_MIN_VISITS`
(varsayılan 2) **farklı haftalık ziyaretten** gelmesi gerekir; aynı saat-hücresi haftada bir
ziyaret edildiğinden ilk karar değer ilk izlendiğinden en erken **bir hafta** sonra gelir.
`PW_VALUE_DISAPPEARED` hücre medyanının en az `PW_DISAPPEAR_MIN_MEDIAN` (5) olmasını ve
`PW_DISAPPEAR_BUCKETS` (6) ardışık ölçülmüş sıfır bekler. `PW_NEW_VALUE` geçmiş penceresinin
(`PW_NEW_VALUE_HISTORY_DAYS`, 7 gün) tam olmasını bekler. Kova `scope_changed` ise (bir üyelik
değişikliğinden sonraki `PW_SCOPE_CHANGE_GRACE_BUCKETS` = 12 kova) hiçbir karar verilmez.

**Doğrulama:** Değerler okuması her değerin taban durumunu ve öğrenme ilerlemesini verir; kova
okuması kapsam değişikliği ve bakım bayraklarını gösterir.

```
GET /api/v1/property-watches/{id}/values
```

```
GET /api/v1/property-watches/{id}/buckets
```

**Çözüm:** Bekle; öğrenme bir arıza değil, dürüst bir durumdur. Kova `scope_changed` ya da bakımda
ise karar tutulur. Öğrenme hiç ilerlemiyorsa kapsamın trafik aldığını (`parsed_events` ve `coverage`)
doğrula; kapsamda özellik **12 ardışık toplanmış kovada** hiç ayrıştırılmıyorsa (tick'ler arasında
sayılır, `total_events > 0` gerekir) izleme `NOT_APPLICABLE` olur (OPS-R7).

### OPS-R52. Olaylar kaydediliyor ama hiçbir şey sayfa açmıyor

**Belirti:** `/anomalies` içinde mülk izleme olayları (`PW_VALUE`) görünüyor ama bunlara karşılık
gelen bir uyarı yok; olayların yaşam döngüsü yine de ilerliyor.

**Olası neden:** `PW_ALERTING_ENABLED` varsayılanda false'tur; bu **gölge moddur**. Gölge modda
olaylar ve yaşam döngüsü (CANDIDATE → OPEN → RECOVERING → RESOLVED) yazılır ama motor hiçbir uyarı
açmaz, yenilemez, çözmez ya da bildirim kuyruklamaz. Uyarılar olaylar kapalı olduğu için değil,
bilerek açılmaz.

**Doğrulama:** Katalog okumasındaki `alerting_mode` alanı uyarı beklentisini söyler (`shadow` ya da
`alerting`):

```
GET /api/v1/property-watches/catalog
```

`alerting_mode` değeri `shadow` ise uyarı beklenmez.

**Çözüm:** Kullanıcı canlı doğrulamadan sonra isterse `PW_ALERTING_ENABLED` ayarını aç. Önceki bir
dönemden açık kalan bir sayfa, ayar yeniden açılana kadar açık kalır; bir izlemeyi devre dışı bırakmak
ya da silmek açık uyarıları yine de çözer.

### OPS-R53. Silme ya da pin kaldırma 409 ile reddediliyor (bir kural bir değeri sabitliyor)

**Belirti:** `DELETE /api/v1/property-watches/{id}` 409 döndürüyor ve hangi F.1 kurallarının bağlı
olduğunu adlandırıyor; ya da bir değeri serbest bırakan bir pin değişikliği 409 alıyor.

**Olası neden:** Bir F.1 eşik kuralı (özel kural) bu izlemenin sabitlenmiş bir değerini konu olarak
kullanıyor. Yeni yabancı anahtar `ON DELETE RESTRICT`'tir: operatörün kuralı sessizce silinmesin diye
silme reddedilir. Aynı sebeple `watch_version`'ı artıran bir değişiklik (kapsam ya da özellik kümesi)
de — kuralın değeri eski sürümde veri almayı bırakacağı için — bir kural bu değeri sabitliyorken 409
verir.

**Doğrulama:** 409 yanıtı bağlı kuralları (`id`, `name`, `pw_value_id`) adlandırır. Pin değişikliği
de, değişiklikten sonra bu listeyi döner. Bağlı kurallar `/custom-rules` listesinde görünür.

```
PUT /api/v1/property-watches/{id}/pins
```

```bash
./qradar-dash logs
```

**Çözüm:** Önce bağlı kuralı çöz ya da sil (`DELETE /api/v1/custom-rules/{id}`) ya da o değeri
serbest bırak; sonra izlemeyi sil / değiştir. İzleme sayfasındaki "Threshold rules" sekmesi hangi
kuralın hangi değeri tuttuğunu gösterir.

### OPS-R54. İzleme `STALE` gösteriyor (en yeni arama başarısız oldu)

**Belirti:** Bir izleme `STALE` gösteriyor; gerekçe, adı geçen kovayı `NOT_COLLECTED` olarak
işaretliyor.

**Olası neden:** En yeni kovanın araması başarısız oldu ya da zaman aşımına uğradı ve bu tick'te
ondan yeni hiçbir kova toplanmadı. Kova depoya `NOT_COLLECTED` olarak yazılır; izleme okumaya devam
etmez, `STALE` olur ve gerekçe başarısız kovayı adıyla söyler. Bu, sessiz bir `OK` değildir.

**Doğrulama:** İzleme okuması `last_status`, `last_status_reason` ve son kova kayıtlarını verir;
`doctor` `watermarks` ve `cadence` satırları son çalışmayı gösterir.

```
GET /api/v1/property-watches/{id}
```

```
GET /api/v1/property-watches/{id}/buckets
```

**Çözüm:** QRadar'a erişimi ve aile anahtarını doğrula (OPS-R46). Sonraki toplanan kova durumu
temizler; `STALE` yalnız bir sonraki kova yazılana kadar sürer. Başarısız aramaları `./qradar-dash
logs` ile incele.

### OPS-R55. Geçmiş okuması takılı görünüyor

**Belirti:** `/admin/history-backfill` sayfasında bir kapsam `READING` durumunda ama okunan gün ya
da kova sayısı ilerlemiyor; ya da sayfa "not decided" deyip duruyor.

**Olası neden:** Üç ayrı durum aynı görüntüyü verir. Birincisi: konsolun kararı yoktur; kurulum
soruyu sormadıysa ya da yanıt dosyasında `HISTORY_BACKFILL` anahtarı yoksa ilk yönetici oturum
açmasında sorulur. İkincisi: canlı geçiş tick'in arama bütçesini tüketmiştir, geçmiş adımı başlayacak
boşluk bulamamıştır. Üçüncüsü: ailenin kendi toplama anahtarı kapalıdır, ya da konsolun `No` yanıtı
vardır. `READING` `history_backfill_max_hours` (varsayılan 72 saat) boyunca sürerse okuma kendiliğinden
`PARTIAL` olur.

**Doğrulama:** Sayfa her konsol için kararı, kapsam durumunu, gerekçeyi ve "sonradan Yes mümkün
mü" satırını gösterir. Karar satırındaki `No` ya da eksik kayıt sorunun kendisidir.

```
GET /api/v1/admin/history-backfill
```

**Çözüm:** Karar yoksa ilk yönetici oturum açmasında soruyu yanıtla. Bütçe dolarsa ailenin
kadansını `/admin/search-cadence` sayfasından doğrula ve `HISTORY_BACKFILL_SEARCHES_PER_TICK` ile
`HISTORY_BACKFILL_SECONDS_PER_TICK` değerlerini büyüt. Aile anahtarı kapalıysa
`WAF_COLLECTION_ENABLED`, `IPS_COLLECTION_ENABLED`, `CATCHALL_COLLECTION_ENABLED` ya da
`RULE_MATCH_COLLECTION_ENABLED` değerini aç. Hiçbiri işe yaramazsa "Stop reading" ile durdur; okunan
veriler kalır.

### OPS-R56. Bir WAF ya da IPS kaynağı `NOT_ELIGIBLE` gösteriyor

**Belirti:** Kaynak satırı `NOT_ELIGIBLE` durumunda ve gerekçesi "this source already collected
live data; history cannot be read before it".

**Olası neden:** WAF ve IPS kaynaklarının geçmişi, kendi canlı verilerinin önüne konulamaz: bir
endpointin ilk görülme zamanı, değerlendirilmiş kovalarda ve açık olaylarda kullanılıyor olurdu.
Kaynak yanıttan **önce** toplamaya başladıysa geçmişi okunamaz.

**Doğrulama:** Sayfadaki "sonradan Yes mümkün mü" sütunu bunu söyler: "not possible: this source
already collected".

```
GET /api/v1/admin/history-backfill
```

**Çözüm:** Profili devre dışı bırak, sonra yeniden oluştur; yeni profil kendi ilk toplamasında
geçmişi okur. Kaynak başka bir profille çalışıyorsa o profil için de aynısı geçerlidir.

### OPS-R57. Okuma `PARTIAL` bitti ve gerekçede "had no event in the window" var

**Belirti:** Kaynak hacmi ya da catch-all okuması `PARTIAL` bitiyor, gerekçe "N sources had no
event in the window" diyor.

**Olası neden:** Okuma konsolun en eski **canlı** kovasında biter. O andan sonra eklenen bir kaynağın
ya da o andan sonra izlemeye alınan bir kaynağın, pencerede hiç olayı yoktur; bu bir sessiz kaynak
sayılır ve okumayı `PARTIAL` yapar. Gerekçe yalnız sayı verir, kaynak adı vermez.

**Doğrulama:** Sayfa kapsamın okunan günlerini ve gerekçesini gösterir. Çapa, konsolun en eski canlı
kovasıdır.

**Çözüm:** Bu bir arıza değil, dürüst bir durumdur. Kaynağın pencerede olayı yoksa geçmişi yoktur.
Okuma bittikten sonra "Read history now" ile yeniden okutmak, konsol düzeyindeki ailelerde yeni
satırlar getirir; WAF ve IPS için yalnız henüz toplamamış kaynaklar okur.

### OPS-R58. Kurulum geçmiş yanıtını kaydetmedi

**Belirti:** Kurulum özeti "History backfill not recorded (exit N)" diyor; konsol kararsız kalıyor.

**Olası neden:** `python -m app.cli.history_backfill decide` komutu başarısız oldu. Çıkış kodu `1`
veritabanı hatası, `2` konsolun kayıtlı olmaması ya da argüman hatasıdır. Kurulum bu hatada durmaz;
karar kaydedilmemiş olarak kalır ve ilk yönetici oturum açmasında soru sorulur.

**Doğrulama:** Komutu elle çalıştırıp çıkış kodunu oku:

```bash
docker compose run --rm --no-deps -T backend \
  python -m app.cli.history_backfill decide \
    --instance <konsol-adı> --answer yes --source installer
```

**Çözüm:** Veritabanının hazır olduğundan ve konsol adının `qradar add` ile kaydedilmiş olduğundan
emin ol, sonra komutu yeniden çalıştır. Kayıtlı bir karar varsa komut onu değiştirmez, "already
decided on <tarih>: <yanıt>" yazar ve 0 ile çıkar.

### OPS-R59. Mülk izlemesinin geçmiş okuması ilerlemiyor

**Belirti:** İzleme sayfasında (`/behavior/watches/<id>`) izleme "Reading history" durumunda ama
"History" bölümündeki okunan gün sayısı ilerlemiyor; durumun yanındaki satır "X of Y days read"
diyor ve sayı değişmiyor. "History gaps" listesi boş da olabilir.

**Olası neden:** Beş ayrı neden aynı görüntüyü verir. Birincisi: canlı toplama tick'in arama
bütçesini tüketmiştir, geçmiş adımı arama yapacak boşluk bulamamıştır; gerekçesi "waiting for
search budget" olur. İkincisi: `PW_BOOTSTRAP_SEARCHES_PER_TICK` bir adımın maliyetinden küçüktür,
yani okuma hiç arama yapamaz; gerekçesi "PW_BOOTSTRAP_SEARCHES_PER_TICK is 2; reading history for
this watch needs at least 3 searches per tick (TOTALS, PINNED and one GROUPED)" olur. Üçüncüsü:
sağlayıcı aramaları başarısız oluyordur; genişlik her başarısızlıkta yarıya iner, en dar genişlikte
(12 kova) `PW_BOOTSTRAP_MAX_FAILURES` (varsayılan 3) kez başarısız olunca o aralık boşluğa dönüşür
("search failed 3 times at the minimum width"). Dördüncüsü: yoğunluk kapısı okumayı kapattı ("% of
the buckets read are lower bounds"). Beşincisi: okuma `PW_BOOTSTRAP_MAX_HOURS` (varsayılan 72 saat)
sınırına dayandı; okuma `PARTIAL` olur ve gerekçesi "the reading did not finish in 72 h" der.

**Doğrulama:** Sayfanın "History" bölümü durumu, istenen pencereyi, okunan aralığı ve boşlukları
gösterir; durumun yanındaki satır okunan gün sayısını verir. API'nin `history` bloğu aynı
sayıları ve gerekçeyi verir.

```
GET /api/v1/property-watches/{id}
```

**Çözüm:** Bütçe bittiyse etkin izleme sayısını azalt ya da `PW_MAX_SEARCHES_PER_TICK` (40),
`PW_BOOTSTRAP_SEARCHES_PER_TICK` (12) ya da `PW_BOOTSTRAP_SECONDS_PER_TICK` (180) değerlerini
büyüt. Ayar bir adımın maliyetinden küçükse (`PW_BOOTSTRAP_SEARCHES_PER_TICK` en az 2, sabitlenmiş
bir değer varsa 3 olmalı) önce onu büyüt. Aramalar başarısız oluyorsa QRadar erişimini ve token'ı
denetle (OPS-R1 ... OPS-R4) ve `./qradar-dash logs` ile arama hatalarına bak. Yoğunluk kapısı ve
zaman sınırı bir arıza değildir; ikisi de okumayı `PARTIAL` bitirir ve izleme canlı toplamaya
devam eder. Bir izlemenin geçmişi yalnız bir kez istenebilir: yeniden okumak isteyen operatör yeni
bir izleme kurar.

### OPS-R60. Geçmiş isteği 409 ile reddediliyor

**Belirti:** Geçmiş isteyen etkin bir izlemeyi etkinleştirmek 409 döndürüyor; gerekçe "reading
history needs at least 3 free searches per tick (TOTALS, PINNED and one GROUPED), but 1 are left
after the enabled watches' live cost of 39 of 40" diyor. Aynı 409, etkin bir izlemede "Read
history" düğmesine basınca da gelir. Yanıtın gövdesi `codes` de taşır: `HISTORY_BUDGET` ya da
başka bir kod (`ENABLED_CAP`, `GROUP_TOO_LARGE`, `WATCH_SEARCH_LIMIT`, `TICK_BUDGET`).

**Olası neden:** Canlı toplama, tick'in arama bütçesini (`PW_MAX_SEARCHES_PER_TICK`, 40) önce
harcar; geçmiş adımı kalan boşluğu harcar. Bir adım sabitlenmiş (pinned) değer varsa üç arama
(TOTALS, PINNED, bir GROUPED), yoksa iki arama tutar. Canlı maliyet düşüldükten sonra kalan arama
sayısı bu maliyetten azsa istek reddedilir. Reddedilen sebep geçmiş bütçesiyse sayfa "Enable
without history" düğmesini gösterir; başka bir sebep varsa (aktif izleme üst sınırı, grup çok
büyük, tick bütçesi) düğme görünmez, çünkü aynı deneme yine reddedilir.

**Doğrulama:** 409 yanıtının metni gerekli arama sayısını, kalan sayıyı ve canlı maliyeti söyler;
`codes` alanı reddedilen sebebin kodunu taşır. Aynı sebep düzenleyicide de görünür: "History
needs 3 free searches per tick for this watch; 1 is free."

```
POST /api/v1/property-watches/{id}/history
```

**Çözüm:** Bir izlemeyi devre dışı bırak (canlı maliyeti düşer) ya da `PW_MAX_SEARCHES_PER_TICK`
değerini büyüt. Olmuyorsa ve reddedilen tek sebep geçmiş bütçesiyse "Enable without history"
düğmesine bas: izleme `history_days = 0` ile etkinleşir, yalnız ileriye doğru öğrenir ve QRadar'ın
geçmişi okunmaz. Başka bir sebep reddedildiyse o düğme yoktur; o sebebi gider (aktif izleme
sayısını azalt, grubu küçült) ve yeniden dene.

### OPS-R61. Bir anomali kovası değerlendirilemedi

**Belirti:** `anomaly_evaluation` FAIL ya da WARN veriyor. FAIL: son 24 saatte en az bir kova
verdict üretmeden bırakıldı ("N anomaly bucket(s) were not judged"). WARN: açık bir fault var (bir
kova yeniden deneniyor) ya da bir izlenen kaynağın beklenen yargılama zamanı geçmiş canlı bir kovası
var — yani o kovanın beklenen yargılama zamanı (kova sonu + toplama yerleşme süresi + bir beat +
iki değerlendirme tick'i) geçmiş. Bir kaynak yalnız bu koşul sağlandığında sayılır; sadece yeni bir
canlı kova olması yeterli değildir.

**Olası neden:** `AnomalyEngine.evaluate_pending` bir kovayı değerlendirirken istisna aldı. Hata
kalıcıysa motor `ANOMALY_MAX_BUCKET_ATTEMPTS` (varsayılan 3) denemeden sonra o kovadan vazgeçer:
`anomaly_evaluation_fault` satırı `given_up_at` ile kapanır, `evaluated_through` o kovanın ötesine
ilerler ve sonraki kova aynı tick'te değerlendirilir. Vazgeçilen kova hiçbir verdict yazmaz:
`log_source_detector_state` ve `log_source_anomaly` içinde o kova için satır yoktur, açık bir olay
olduğu gibi kalır. Yani "satır yok" "anomali yok" demek değildir. Hata geçiciyse (kilit, zaman
aşımı, yeniden başlatma) sonraki başarılı kova fault satırını `resolved_at` ile kapatır.

**Doğrulama:** `./qradar-dash doctor` satırı kaynak sayısını, son hata sınıfını ve zamanını verir.
Kovanın kendisi ve sınıfı backend günlüğündedir; satır artık `bucket_start`, `attempts`,
`given_up` ve `error_class` alanlarını taşır. Açık fault'lar:

```sql
SELECT log_source_id, bucket_start, attempts, error_class, first_failed_at, last_failed_at
FROM anomaly_evaluation_fault
WHERE resolved_at IS NULL AND given_up_at IS NULL
ORDER BY last_failed_at DESC;
```

Son 24 saatte vazgeçilenler:

```sql
SELECT log_source_id, bucket_start, attempts, error_class, given_up_at
FROM anomaly_evaluation_fault
WHERE given_up_at >= now() - interval '24 hours'
ORDER BY given_up_at DESC;
```

**Çözüm:** `error_class` bir kod hatasıysa (RuntimeError, KeyError, ValueError) bu bir yazılım
hatasıdır; günlükteki aynı `bucket_start` satırını stack trace ile birlikte bildir. Vazgeçilen kova
"not judged" olarak kalır; geriye dönük verdict üretilmez ve bu doğrudur. Veritabanı kaynaklı
geçici sınıflar (OperationalError, TimeoutError, DeadlockDetected) kendiliğinden düzelir; sayı
artmıyorsa işlem yapma. Sayı sürekli artıyorsa OPS-R36 ve OPS-R14'e bak.
`ANOMALY_MAX_BUCKET_ATTEMPTS` değerini yükseltmek yalnız yavaş bir veritabanında anlamlıdır;
kalıcı bir kod hatasını gizlemez.

```bash
./qradar-dash logs
```

### OPS-R62. `doctor` bilinmeyen bir ayar adı yazıyor

**Belirti:** `doctor` `unknown_settings` için WARN yazar: `ignored unknown settings:` ve ardından
büyük harfli adlar. Backend başlangıcında bir WARNING satırı aynı adları yazar (`ignoring unknown
settings:`). Değerler yazılmaz.

**Olası neden:** Ortamda, bir ayarın ilk `_` öncesi parçasıyla başlayan ama kendisi bir ayar
olmayan bir ad vardır. Sık neden yazım hatasıdır (`CATCHALL_COLLECTION_ENABLE` gibi). Bildirim
kanallarının `NOTIFY_*` ve `SMTP_*` adları ile `GIT_SHA` bu listeye girmez; onlar bilerek
`Settings` dışında okunur. `./qradar-dash install` komutunun `.env` dosyasına yazdığı adlar
(`QRADAR_DASH_VERSION` gibi) ve compose dosyalarındaki `${...}` başvuruları da girmez.

**Doğrulama:** `doctor` satırı adları listeler. Başlangıç günlüğü aynı adları yazar.

```bash
./qradar-dash doctor
./qradar-dash logs
```

**Çözüm:** Adı `.env` içinde düzelt ya da sil. `install` bilinmeyen `KEY=VALUE` satırlarını
korur, bu yüzden yazım hatası yeniden kurulumdan sonra da durur. Ayar reddedilmez; yok sayılır
ve burada bildirilir.

### OPS-R63. Bir aile geç kalıyor ve gerekçe Ariel izni diyor

**Belirti:** Bir ailenin toplaması geç kalıyor. Filigranın `last_error` değeri
`ArielPermitUnavailable` sınıfını taşıyor, ya da geçmiş okumanın gerekçesi
`deferred: no Ariel permit` diyor.

**Olası neden:** Ariel izin havuzu dolu. Canlı iş bekler. Geçmiş okuma yerini verir.

**Doğrulama:** `doctor` içindeki `ariel_permits` satırı global havuzun held/cap değerini yazar.
Doluysa WARN. `GET /api/v1/searches/ariel-budget` aynı sayıları döner. Yanıtta sorgu metni yoktur.

```bash
./qradar-dash doctor
```

**Çözüm:** Yükü düşür: kadansları seyrelt ya da geçmiş okumayı azalt. Tavanları, cihazın
kaldırdığı ölçüde yükselt. Ölçülmüş sınırlar AP-8'dedir. İzni kapatarak çözme.

### OPS-R64. MCP çağrıları başarısız oluyor

**Belirti:** `mcp` profili açık ve bir MCP çağrısı iki sağlayıcı hatasından biriyle düşüyor:

- `MCP service rejected the request`: sağlayıcının auth hatası. MCP servisi 401 ya da 403 döndü.
- `MCP session initialisation failed upstream`: sağlayıcının erişilemezlik hatası. MCP servisi
  oturum açılırken 4xx/5xx döndü; TLS hatasında bu HTTP 500'dür.

Servise hiç ulaşılamazsa hata bunlardan biri değil, `Could not reach the MCP service` olur.
Servis ayakta değilse ya da profil `COMPOSE_PROFILES` içinde yoksa önce `docker compose ps
qradar-mcp` ile konteynerin çalıştığına bak.

**Olası neden:** Dördü de `qradar-mcp` ile konsol arasındadır:

1. `qradar_ca.pem` yanlış ya da eksik: zincirin yalnız bir parçası var, ya da konsolun CA'sı değil.
2. Konsol sertifikasının SAN alanlarında `QRADAR_CONSOLE_FQDN` değeri yok.
3. `internal` ağdan konsola rota yok (bkz. OPS-R4; macvlan override'ında `qradar-mcp` da vardır).
4. Token eksik ya da yanlış: backend `SEC` başlığına boş ya da geçersiz değer koyuyor.

Sunucunun kendi günlüğündeki `Authentication failed for both user and service` satırı **hem bozuk CA
hem eksik ya da yanlış token için aynıdır**. TLS hatası backend'e auth hatası olarak değil, HTTP 500
ve erişilemezlik olarak ulaşır. Bu yüzden satır tek başına nedeni söylemez; aşağıdaki TLS denetimiyle
birlikte okunur.

**Doğrulama:** Sırayla. Hiçbir komut token yazdırmaz ve hiçbiri bir şeyi değiştirmez.

1. Sunucunun günlüğü. Sunucu yalnız standart çıktıya yazar; `mcp_logs` birimi boş kalır.

   ```bash
   ./qradar-dash logs --tail=200 qradar-mcp
   ```

2. Konteynerin içinden TLS denetimi. Komut, sunucunun kullandığı aynı CA dosyasını
   (`REQUESTS_CA_BUNDLE`) ve aynı adı (`QRADAR_CONSOLE_FQDN`) kullanır; imajda hiçbir şey kurmaz.
   Yalnız el sıkışır, token ve istek göndermez.

   ```bash
   docker compose exec qradar-mcp python -c 'import os,socket,ssl; h=os.environ["QRADAR_CONSOLE_FQDN"]; c=ssl.create_default_context(cafile=os.environ["REQUESTS_CA_BUNDLE"]); s=c.wrap_socket(socket.create_connection((h,443),10),server_hostname=h); print("TLS OK",s.version(),s.getpeercert()["subjectAltName"])'
   ```

   - `TLS OK ...` ve SAN listesi: CA ve ad doğru; geriye neden 4 kalır (neden 3 aralıklı da olabilir).
   - `CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`: neden 1.
   - `CERTIFICATE_VERIFY_FAILED ... Hostname mismatch, certificate is not valid for '<ad>'`: neden 2.
   - `TimeoutError` ya da `OSError`: neden 3.

   Ayrıntılı zincir dökümü gerekirse imajda `openssl` vardır; ana makinedeki OPS-R2 komutunun
   aynısı `docker compose exec qradar-mcp openssl s_client ...` ile de çalışır.

3. Token tarafı, ancak 2. adım `TLS OK` verdiyse: `./qradar-dash doctor` içindeki `qradar_auth`
   satırı backend'in aynı token'la yaptığı kendi QRadar denetimidir. FAIL ise token sorunu OPS-R1'dedir.
   PASS ise ve MCP yine `rejected the request` veriyorsa, MCP için kayıtlı konsolun saklı token'ı
   eskidir (`qradar add` ile yeniden kaydet).

```bash
./qradar-dash doctor
```

**Çözüm:**

- Neden 1: Kökü, sonra ara sertifikaları `.secrets/qradar-ca.pem` dosyasında birleştir; sonra
  `./deploy/load-secrets.sh` çalıştır ve `docker compose restart qradar-mcp` ile servisi yeniden
  başlat (OPS-R2). Konsol sertifikasında AKI yoksa backend için `QRADAR_TLS_ALLOW_MISSING_AKI=true`
  gerekir (OPS-R3); sunucunun Python'u AKI'siz bir CA'yı kabul etti.
- Neden 2: `.env` içindeki `QRADAR_CONSOLE_FQDN` değerini sertifikanın bir SAN değerine eşitle
  (IP SAN varsa IP de olur) ve `docker compose up -d qradar-mcp` ile konteyneri yeniden yarat.
  Ya da konsola SAN'ı olan bir sertifika düzenlet.
- Neden 3: OPS-R4'teki macvlan override'ını ya da bir rotayı ekle; `qradar-mcp` konsola `internal`
  ağdan ulaşabilmeli.
- Neden 4: `.secrets/qradar.sec` dosyasını değiştir, `./deploy/load-secrets.sh` çalıştır ve OPS-R1'deki
  `qradar add` komutunu yeniden çalıştır. MCP servisi token'ı hiç saklamaz; her istekte backend'den alır.

Token hiçbir komutta yazdırılmaz; günlüklerde de yoktur.

### OPS-R65. Kural-eşleşme pencereleri `FAILED` veya `TRUNCATED` kalıyor

**Belirti:** `doctor`, `rule_match_coverage` için WARN verir. Satır son 30 günlük sağlık
aralığındaki başarısız ve kesilmiş pencere sayılarını, en eskisini, onun aralıktan çıkacağı UTC
zamanını, otomatik onarıma hâlâ uygun olanları ve catch-up ufku nedeniyle hiç aranmadan atlananları
yazar. Bu pencereler aralıkta kaldıkça sessiz EVENT kuralları `NEVER_OBSERVED` yerine
`INSUFFICIENT_DATA` kalır.

**Olası neden:** Ariel araması canlı denemeleri ve onarım denemelerini tüketmiştir; ya da worker
kesintisi catch-up ufkunu aşmış ve pencereler hiç aranmadan atlanmıştır. `TRUNCATED`, tek pencerenin
satır sınırına ulaştığını gösterir ve otomatik onarımın kapsamı dışındadır.

**Doğrulama:** Önce QRadar erişimini ve Ariel izinlerini doğrula; hata metni değil, sınıfı worker
günlüğündedir.

```bash
./qradar-dash doctor
./qradar-dash logs --tail=200 celery-worker
```

**Çözüm:** Ariel erişimini düzelt ve onarım sınırındaki pencereler için sonraki kural-eşleşme
kadansını bekle. Onarım yalnız `RULE_MATCH_REPAIR_MAX_AGE_SECONDS` içindeki yakın `FAILED` canlı
pencereleri, sınırlı sayıda dener. Catch-up ufku nedeniyle atlanan pencere yeniden okunmaz: olaylar
QRadar'dan dönmüş olabilir ve boş bir okuma sessizliğin kanıtı sayılamaz. Böyle bir pencere için
`doctor` satırındaki UTC çıkış zamanını bekle; daha yeni başka pencereler varsa kendi başlangıç
zamanlarından `RULE_HEALTH_WINDOW_DAYS` sonra ayrı ayrı çıkarlar. Satır `TRUNCATED` diyorsa satır
sınırı ve sorgu yükü ayrı bir tasarım gerektirir; bu onarım onu sağlıklı saymaz.
