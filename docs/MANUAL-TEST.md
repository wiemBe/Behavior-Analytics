# QRadar-Dash manuel test kılavuzu

Bu kılavuz, bir QRadar-Dash sürümünü canlıya almadan önce, tek başına ve adım adım yürütülecek elle testi anlatır. Komutlar, günlük
satırları, dosya yolları, ayar adları, HTTP durum kodları ve `doctor` kimlikleri kaynaklardaki gibi bırakılmıştır. Çevrilmemiştir.
Arayüz etiketleri, Türkçe kataloğun (`frontend/messages/tr/*.json`) gösterdiği biçimde tırnak içinde yazılmıştır; test iki dilde yapıldığı için
İngilizce karşılığı ilk geçtiği yerde parantez içinde verilmiştir.

Dört kural her bölümde geçerlidir ve bu test onlara uyar:

> 1. Alembic downgrade hiçbir zaman kullanılmaz. Geri alma, bir yedeğin geri yüklenmesidir.
> 2. SELinux hiçbir zaman kapatılmaz ve `:z` hiçbir zaman kullanılmaz.
> 3. Sırlar hiçbir zaman `.env` dosyasına, kabuk geçmişine, bir bilete, destek paketine ya da sonuç sayfasına girmez.
> 4. Bu araçta hiçbir şey QRadar'a yazmaz. Araç QRadar'a karşı salt okunurdur.

Bu testte QRadar'ın yapılandırmasını değiştiren hiçbir adım yoktur. QRadar'a giden tek yazma isteği, uygulamanın kendi Ariel aramasını oluşturması ve iptal etmesidir (bölüm 14).

Sonuçlar `docs/MANUAL-TEST-RESULTS.md` sayfasına yazılır. Bu kılavuz sürüme göre bölümlenir: her sürüm kendi bölümünü taşır; yeni bir sürüm,
kendi bölümünü bu bölümün **üstüne** ekler.

**İçindekiler**

1. [0.1.7](#017)

---

## 0.1.7

Bu bölüm 0.1.6 sitesini 0.1.7'ye yükseltir ve yükseltilmiş siteyi sınar. Üretim sitesi bugün 0.1.6'dadır (migrasyon başı `0032`). 0.1.7'nin migrasyon başı `0044`'tür (`/api/v1/version` içindeki `migration_head`). Üretim IPS kaynakları Cisco Firepower/FTD ve Palo Alto Threat'tir.

### Bu sürüm neyi değiştirir

- **Arayüz dili.** Arayüz İngilizce ya da Türkçe çalışır. Dil, Hesap ayarları sayfasından seçilir; hesapta (`ui_language`) ve tarayıcıda (`ba_locale` çerezi) saklanır.
  Backend'in yazdığı cümlelerin büyük bölümü artık bir kodla birlikte saklanır ve Türkçe okunur: Phase A anomali açıklamaları ve yaşam döngüsü nedenleri, WAF ve IPS anomali cümleleri,
  özellik izleme cümleleri, kural sağlığı ve coverage nedenleri, uyarı başlıkları, özel kural durum nedenleri, arama zamanlama ve çalıştırma iletileri, geçmiş okuma cümleleri,
  reddetme (403/409/422) iletileri. İngilizce, API'nin metnidir ve değişmez.
- **Migrasyonlar eklemelidir.** `0033` ... `0044` yeni sütunlar ekler. `0033` dışındakiler boş (`NULL`) kabul eder; `0033`, `app_user.ui_language` sütununu ekler ve var olan her hesap `en` değerini alır. Var olan satırlar değişmez, hiçbir şey silinmez ve geri doldurma yoktur.
  0.1.6'nın yazdığı saklı cümleler İngilizce kalır; Türkçede de İngilizce görünür (bölüm 4).
- **Bu sürümde olmayanlar.** Motorun yazdığı uyarı başlıkları ve "condition recovered" nedeni (MSG-7b) İngilizce kalır; geçmiş okumadaki Ariel erteleme cümlesi de İngilizce kalır (MT-59). Bu beklenir, hata değildir.

### Nasıl kullanılır

- **Üretimde yalnızca çekirdek yürütülür.** Başlığında `[çekirdek]` yazan 38 kontrol üretim yoludur; tam liste laboratuvar koşusu içindir (MT-1b) ve üretimde yalnızca operatörün zamanı varsa yapılır. Çekirdekte (yazar) olarak yalnızca ön koşulların ve yükseltmenin gerektirdiği adımlar (yedek, destek paketi, yükseltme, dil tercihi) vardır; çekirdekte `yalnızca lab` kontrol yoktur.
- Kontrollerin sırası bölüm sırasıdır. Her kontrolün **Ön koşul**, **Eylem**, **Beklenen** ve **Kaydet** alanı vardır. Sonucu sonuç sayfasına `Geçti`, `Kaldı` ya da `Koşulmadı` olarak yaz; `Kaldı` ve `Koşulmadı` için Not yaz.
- **(yazar)**: kontrol uygulama durumunu değiştirir (bir dosya, bir hesap, bir kayıt yazar). Her birinin **Temizlik** adımı vardır.
- **yalnızca lab**: kontrol, analistlerin gördüğü durumu değiştirir ya da silinemez bir iz bırakır; üretimde `Koşulmadı` yazılır, nedeni kontrolde belirtilmiştir. Orkestratör aynı kılavuzu bir laboratuvar VM'sinde (MT-1b) harfiyen yürütür.
- **QRadar araması**: kontrol QRadar'a bir Ariel araması gönderir ve konsol kapasitesi harcar. Bu testte üç kontrol böyledir ve üçü de `yalnızca lab`'dır.
- **konsol yöneticisi adımı**: kontrolü QRadar konsolunun yöneticisi yapar.
- `Koşulmadı`: kontrol yapılamadı (örneğin sitede o türde veri yok). "Veri yok" Kaldı değildir; Not'a nedenini yaz.
- Hiçbir adımda sır yazılmaz (bölüm 16). Parolaları ve `.env` değerlerini sayfaya ya da bilete koyma.
- Hiçbir adımda SELinux kapatılmaz ve `:z` kullanılmaz.

### Zamanlama ve toplam süre

Yükseltmeden sonra toplama, ailelerin kadansında yeniden başlar. Varsayılan kadans her aile için 5 dakikadır; WAF ve IPS için 15 dakikadır. Sitenin kendi değerleri `/admin/search-cadence` sayfasındadır.
Bir toplamanın verisinin sayfada görünmesi için kadans ve yerleşme payı (`SEARCH_CADENCE_SETTLE_SECONDS`, varsayılan 120 sn) geçmelidir; `doctor` ayrıca 60 sn pay verir. Bu yüzden:

| Ne | Ne zaman bakılır | Daha erken bakılırsa sayfa ne gösterir |
|---|---|---|
| Sürüm, migrasyon, `doctor` (bağımsız satırlar), giriş, dil, kılavuz, yönetim sayfaları | Hemen | (veriye bağlı değil) |
| Pano bileşenleri, anomali ve filo verisi, kural ve coverage nedenleri | Ailenin kadansı + 3 dk (Phase A: 8 dk; WAF ve IPS: 18 dk) | “Güncel değil” ya da “Toplanmadı”; doğrudur, Kaldı yazma |
| Yeni Türkçe satırlar (özellik izleme nedeni, özel kural nedeni, kural sağlığı nedeni, geçmiş okuma nedeni) | Satırı yazan toplamanın bir sonraki çalışmasından sonra | Saklı İngilizce metin |
| Yeni geçiş, yeni anomali, yeni uyarı | Bir olay olunca; beklenmez | Eski satırlar (İngilizce) |

**Çekirdek (üretim yolu):** 38 kontrol, yaklaşık **2 saat** etkin çalışma (bir tahmindir, ölçülmedi) ve konsol yöneticisinin yaklaşık 10 dakikası (MT-120); kadans beklemesi ayrıdır. **Üretim sırası:** çekirdek kontroller kimlik sırasıyla yürütülür; kadans beklemesi (en çok 18 dk) MT-12 ile MT-22 gibi yeni veri gerektirmeyen salt okunur kontrollerle doldurulur, veriye bağlı kontroller (MT-24 ve sonrası) bekleme dolunca yapılır.

Tam liste: yaklaşık **6,5 saat** etkin çalışma (bölüm 4'teki üç tur ve bölüm 5'teki 20 bileşen en uzun iki parçadır), bekleme süreleri dışında. Bölümlere göre: Ön koşullar 20 dk, Yükseltme 20 dk (kesinti pencere içindedir),
Oturum açma 20 dk, Dil 120 dk, Pano 40 dk, Anomaliler 30 dk, Uyarılar 10 dk, Özel kurallar 15 dk, Aramalar 15 dk, Geçmiş okuma 10 dk, Kural/kapsam/filo 35 dk, Yönetim 25 dk, Kılavuz 5 dk, QRadar'a karşı salt okunur 15 dk (konsol yöneticisi ayrı), Geri alma ölçütü 5 dk.
İlk kadans beklemesini (en çok 18 dk) Oturum açma ve Dil bölümleriyle doldur. İki yarım günde de yapılabilir; yükseltme ile Pano ve anomali kontrolleri arasında bekleme gerekir.


### 1. Ön koşullar

Bu bölüm yükseltmeden önce yapılır. Her kontrolün sonucu sayfadaki tabloya yazılır. Bir ön koşul Kaldı ise yükseltmeye başlama.

#### MT-01 — Yükseltmeden önceki sürüm ve sağlık [çekirdek]

**Eylem:**


```bash
./qradar-dash status
curl -s http://127.0.0.1:8000/api/v1/version
curl -s http://127.0.0.1:8000/api/v1/health/ready
```

- Betik `.env` içindeki `API_LOOPBACK_PORT` değerini kullanır (varsayılan 8000). Port farklıysa onu yaz.

**Beklenen:**

- `status`: `Installed version: 0.1.6`; `API ready: HTTP 200 (http://127.0.0.1:8000/api/v1/health/ready)`; `docker compose ps` tablosunda her servis çalışıyor ve sağlıklı; `Last upgrade:` satırı yazılıdır; çıkış kodu 0.
- `/api/v1/version`: `"version":"0.1.6"` ve `"migration_head":"0032"`.
- `/api/v1/health/ready`: `"status":"ready"`, `"database":"ok"`, `"revision":"0032"`.

**Kaydet:** `Installed version`, `Last upgrade:` satırı, `git_sha` ve `migration_head` değerleri.

#### MT-02 — `doctor` yükseltmeden önce yeşil [çekirdek]

**Eylem:**


```bash
./qradar-dash doctor
echo "çıkış: $?"
```


**Beklenen:**

- Hiçbir satır `FAIL` değildir. Çıkış kodu 0 (hepsi PASS) ya da 1 (en az bir WARN, FAIL yok). 2 ise yükseltme yapılmaz; satırın `runbook: OPS-R<n>` girişine bak.
- Sağlıklı bir sitede şu satırlar `WARN` olabilir ve yükseltmeyi engellemez; her birini ve nedenini Not alanına yaz: `backup_age` (yedek yok ya da 26 saatten eski; sonraki kontrol bunu düzeltir), `disk_docker` ve `disk_backup` (%70 ile %89 arası dolu), `qradar_tls` (sertifika 30 günden yakın bitiyor), `cadence`, `errors_recent` (bir ya da iki hatalı filigran), `anomaly_evaluation`, `rule_match_coverage`, `ariel_permits` (havuz dolu). Bunlar yükseltmeden önce de doğrudur.
- `WARN` olursa dur ve sahibine bildir: `selinux` (`SELinux is permissive`: SELinux'a dokunma), `host_clock`. `families_off` her zaman PASS'tir. `SKIP` satırı bir özelliğin kapalı olduğunu söyler ve çıkış kodunu yükseltmez.
- Satır biçimi: `PASS  db_connect         database accepted SELECT 1`. Bu aşamada `db_connect`, `migration_head` (`database and image are at 0032`), `containers`, `celery_workers`, `celery_beat`, `qradar_auth`, `encryption` ve `secrets_files` PASS olmalıdır.

**Kaydet:** Çıkış kodu, `WARN` olan her satırın kimliği ve `FAIL` varsa kimliği.

#### MT-03 — Doğrulanmış yedek (yazar) [çekirdek]

**Ön koşul:** `doctor` (önceki kontrol) FAIL değil. Yedek kökünde boş alan var (`disk_backup`).

**Eylem:**


```bash
./qradar-dash backup manual
```

- Komut son satırda yedek dizininin yolunu yazar (`<yedek-kökü>/qradar-dash-<utc>-manual`). Yolu al ve `MANIFEST` dosyasını oku:

```bash
BACKUP=<yazılan yol>
ls -l "$BACKUP"
cat "$BACKUP/MANIFEST"
```

- `--no-verify` verme: yedek, ayrı bir veritabanına geri yüklenip doğrulanır.

**Beklenen:**

- Komut 0 ile çıkar. TimescaleDB için `circular foreign-key constraints` uyarıları yazabilir; bu beklenir, belirleyici olan çıkış koduyla yedeğin var olmasıdır.
- Dizinde `database.dump`, `env.bak`, `secrets.tar`, `state.json` ve `MANIFEST` vardır; dizin izni `0700`, dosya izinleri `0600`.
- `MANIFEST` içinde `VERSION=0.1.6`, `ALEMBIC_VERSION=0032`, `STATE_JSON=included` ve bir `DATABASE_SIZE_BYTES=<sayı>` satırı vardır.

**Kaydet:** Yedek dizininin adı (yalnızca `qradar-dash-<utc>-manual` kısmı), `VERSION` ve `ALEMBIC_VERSION` satırları. `env.bak` ve `secrets.tar` içeriği hiçbir yere yazılmaz.

**Temizlik:** Gerekmez. Yedek, saklama kuralı gereği (son 7 `manual` yedek) kalır; yükseltme ayrıca kendi `pre-upgrade` yedeğini alır.

#### MT-04 — Günlüklerin ve destek paketinin yeri (yazar) [çekirdek]

**Eylem:**


```bash
./qradar-dash logs --tail=20 backend
./qradar-dash support-bundle
```

- `logs`, `docker compose logs` komutuna aldığı argümanları iletir. `support-bundle` yolu yazdırır; içeriğini listele:

```bash
BUNDLE=<yazılan yol>
ls -l "$BUNDLE"
tar -tzf "$BUNDLE" | head -40
```


**Beklenen:**

- `logs` backend'in son 20 satırını yazar ve 0 ile çıkar.
- `support-bundle` `<yedek-kökü>/support-<utc>.tar.gz` yolunu yazar; dosya izni `0600`.
- Arşivde `doctor.txt`, `version.txt`, `compose-ps.txt`, `watermarks.txt`, `cadence.txt`, `config.env` ve `logs/` vardır. `database.dump`, `env.bak`, `secrets.tar` ve `.secrets/` yoktur.

**Kaydet:** Destek paketinin tam yolu (içeriği değil).

**Temizlik:** Test sonunda hiçbir kontrol Kaldı değilse bu dosyayı sil: `rm "$BUNDLE"`. Bir kontrol Kaldı ise sakla ve bölüm 16'daki gibi teslim et.

**Not:** Bir başarısızlık olursa, geri almadan **önce** yeni bir `support-bundle` al ve `logs --tail=2000 backend` çıktısını sakla. Geri alma kayıtları ve bozuk veritabanı kopyasını korur, ama günlüklerin bağlamı kaybolabilir.

#### MT-05 — Pencere ve haberdar edilecek kişiler [çekirdek]

**Eylem:**

- Başlamadan önce şunları sayfanın başına yaz: bakım penceresinin başlangıç ve bitişi (UTC), yükseltme yolu (çevrimiçi ya da çevrimdışı paket), geri alma kararını verecek kişi, QRadar konsol yöneticisi (bölüm 14 için), analistlere duyuru yapıldı mı.

**Beklenen:**

- Yükseltme sırasında backend, worker, beat, frontend ve proxy durur; analistler bağlantı hatası görür. Süre için bölüm 2'ye bak.

**Kaydet:** Pencere başlangıcı ve bitişi (UTC), yolun adı, duyurulan kişi ya da rol.

### 2. Yükseltme 0.1.6 → 0.1.7

Siteyi 0.1.6'dan 0.1.7'ye yükseltmenin iki yolu vardır; birini seç. Hedef sürüm `docs/OPERATIONS.md` içindeki "Yükseltme" ve "Çevrimdışı paket" bölümleriyle aynı biçimde çalışır. Yükseltme, kendi doğrulanmış `pre-upgrade` yedeğini alır; ayrıca yedeğin geri yükleme testi yapılır. Yükseltmeden sonra beklenen migrasyon başı `0044`'tür; 0.1.6 başı `0032`'dir.

**Kesinti.** Backend, worker ve beat, `pg_dump` başlangıcından `/api/v1/health/ready` 200 dönene kadar kapalıdır; `frontend` ve `proxy` de durdurulur. Küçük bir veritabanında yükseltme kesintisi yaklaşık 15-20 sn ölçüldü; 5 GiB altı veritabanı için bütçe 10 dakikadır. İmaj çekme bu pencerenin dışındadır. Bunlar söz değildir. Yükseltme Redis'i boşaltır (`FLUSHALL`); sıradaki görevler kaybolur ve bir sonraki beat tick'inde yeniden kurulur, önemli veri PostgreSQL'dedir. QRadar'a hiçbir şey yazılmaz.

#### MT-06 — Yükseltme, çevrimiçi yol (yazar) [çekirdek]

**Ön koşul:** Doğrulanmış yedek (MT-03) tamam. Çevrimdışı yolu seçtiysen bu kontrol `Koşulmadı` olur.

**Eylem:**


```bash
git fetch --tags
git checkout v0.1.7
./qradar-dash upgrade 0.1.7
```

- Başlangıç ve bitiş saatini `date -u` ile Not alanına yaz.

**Beklenen:**

- Komut 0 ile çıkar ve son satır `Upgrade complete: 0.1.6 -> 0.1.7.` olur.
- Ön kontrolde (yazıcılar durmadan önce) şu üç satır yazılır; baş **kurulu** sürümündür:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0032
Full report: ./qradar-dash doctor
```

- Yeni yığın başladıktan sonra ikinci çift yazılır; baş **hedef** sürümündür:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0044
Full report: ./qradar-dash doctor
```

- `PASS` satırlarındaki boşluk sayısı sürüme göre değişebilir; metin aynıdır.
- Çıkış `upgrade refused: …` ise hiçbir konteyner durmamıştır; nedeni oku (örneğin `this checkout is version <x>, not 0.1.7` ise `git checkout v0.1.7` unutulmuştur) ve düzelt.
- Çıkış sıfırdan farklıysa ve son satır `upgrade to 0.1.7 failed and was rolled back to 0.1.6; the broken database was kept` ise otomatik geri alma çalışmıştır: bölüm 15'e git (OPS-R44).

**Kaydet:** Çıkış kodu, başlangıç ve bitiş saati (UTC), iki `migration_head` satırı, son satır. Kaldı ise ilk hata satırı.

**Temizlik:** Geri alma, bölüm 15'teki `rollback` komutudur. Başka temizlik yoktur.

#### MT-07 — Yükseltme, çevrimdışı paket yolu (yazar) [çekirdek]

**Ön koşul:** Paket, bağlı bir makinede aynı etiketten (`git checkout v0.1.7 && ./qradar-dash bundle`) hazırlanmış ve siteye taşınmıştır. Çevrimiçi yolu seçtiysen bu kontrol `Koşulmadı` olur.

**Eylem:**

- İki makinede de toplamı karşılaştır, sonra kurulum dizininde paketi aç ve yükselt:

```bash
sha256sum /yol/qradar-dash-0.1.7-bundle.tar
cd <kurulum dizini>
tar -xf /yol/qradar-dash-0.1.7-bundle.tar --exclude=images.tar
./qradar-dash upgrade 0.1.7 --offline /yol/qradar-dash-0.1.7-bundle.tar
```


**Beklenen:**

- İki toplam aynıdır.
- Paket doğrulanır ve yüklenir; çıktıda `bundle images loaded and checked against MANIFEST` satırı görünür. Hiçbir konteyner imajlar yüklenip denetlenmeden durdurulmaz.
- Sonrası, çevrimiçi yoldaki gibidir: iki `PASS` çifti (ilki `0032`, ikincisi `0044`) ve son satır `Upgrade complete: 0.1.6 -> 0.1.7.`, çıkış kodu 0.
- Eksik bir imaj varsa `upgrade refused: target images are unavailable: image <imaj> is not loaded on this host; no container was stopped; pass the bundle: …` yazılır ve hiçbir şey durmaz.

**Kaydet:** Her iki toplam, çıkış kodu, başlangıç ve bitiş saati (UTC), iki `migration_head` satırı.

**Temizlik:** Bölüm 15'teki `rollback`.

#### MT-08 — Yükseltmeden sonra sürüm ve durum [çekirdek]

**Eylem:**


```bash
./qradar-dash status
curl -s http://127.0.0.1:8000/api/v1/version
curl -s http://127.0.0.1:8000/api/v1/health/ready
```


**Beklenen:**

- `status`: `Installed version: 0.1.7`; `Last upgrade: success, 0.1.6 -> 0.1.7 at <utc>`; `Last upgrade backup: <yol> (age <yaş>)`; `API ready: HTTP 200 (…)`; çıkış kodu 0; her servis sağlıklı.
- `/api/v1/version`: `"version":"0.1.7"` ve `"migration_head":"0044"`.
- `/api/v1/health/ready`: `"status":"ready"`, `"database":"ok"`, `"revision":"0044"`.

**Kaydet:** Üç çıktının sürüm ve `migration_head` değerleri; `Last upgrade:` satırı.

#### MT-09 — `doctor` yükseltmeden hemen sonra [çekirdek]

**Eylem:**


```bash
./qradar-dash doctor
```


**Beklenen:**

- `FAIL` yoktur. `db_connect` PASS; `migration_head` PASS ve `database and image are at 0044`; `version_match` PASS ve `frontend and backend report 0.1.7`; `containers` PASS.
- Beat ve worker yeni başladığı için ilk dakikada `celery_workers` ya da `celery_beat` geçici FAIL verebilir; bir dakika bekleyip komutu yinele. İkinci çalıştırmada FAIL kalırsa Kaldı.
- Toplama henüz bir kadans çalışmadığı için `cadence`, `anomaly_evaluation` ve `rule_match_coverage` WARN olabilir. Bunlar MT-11 içinde yeniden okunur.

**Kaydet:** Çıkış kodu; WARN ve FAIL olan satırların kimliği; `migration_head` ve `version_match` satırlarının metni.

#### MT-10 — Kesinti süresi ve arayüzün dönüşü [çekirdek]

**Eylem:**

- Yükseltme komutunun başlangıç ve bitiş saatini (MT-06 ya da MT-07) kullan. Tarayıcıda `https://<host>/login` sayfasını aç.

**Beklenen:**

- Yükseltme küçük bir veritabanında yaklaşık bir dakikada biter (4 vCPU'lu bir test makinesinde 50-52 sn ölçüldü; bu bir söz değildir); kesinti penceresi bundan kısadır. 10 dakikayı aşan bir kesinti Not alanına yazılır (veritabanı büyükse dökümün süresi eklenir; bu bir söz değildir).
- Giriş sayfasında “Oturum aç” başlığı ve altında `API sürümü 0.1.7` satırı görünür.

**Kaydet:** Yükseltme süresi (dakika), kesinti süresinin tahmini, giriş sayfasındaki sürüm satırı.

#### MT-11 — İlk kadanstan sonra toplamanın dönmesi [çekirdek]

**Ön koşul:** Yükseltme bitiş saatini biliyorsun. Yönetici hesabıyla oturum açtın (bölüm 3).

**Ne zaman bakılır:** Yükseltmeden sonra, sayfadaki en uzun kadans + 3 dakika (yukarıdaki "Zamanlama ve toplam süre" tablosu). Varsayılan: 5 dk aileler için 8 dk, WAF ve IPS için 18 dk.

**Eylem:**

- `/admin/search-cadence` sayfasını aç (“Arama sıklığı”). Her ailenin “Sıklık” değerini oku ve en uzununu not et.
- Bekleme süresi dolunca sayfayı yenile ve “Son çalıştırma (UTC)” sütununu yükseltme bitiş saatiyle karşılaştır.

```bash
./qradar-dash doctor
```


**Beklenen:**

- Kadansı olan her ailenin “Son çalıştırma (UTC)” zamanı yükseltme bitişinden sonradır; “Sonraki çalıştırma (UTC)” gelecektedir. Hiç çalışmamış bir aile “Henüz çalışmadı” ya da “Yükseltmeden beri çalışmadı” yazar (tire ya da bu metin; sıfır değildir). Kapalı bir aile (örneğin sitede WAF kapalıysa) için bu beklenir.
- Beklemeden önce bakarsan “Henüz çalışmadı” ya da eski bir zaman görürsün: bu hata değildir, Kaldı yazma, bekle.
- `doctor`: `watermarks` PASS, `cadence` PASS (`<n> cadence row(s) are on time or disabled`), `errors_recent` PASS (`no watermark has recent failures`), `celery_workers` ve `celery_beat` PASS. `FAIL` Kaldı'dır; `cadence` WARN ise ailelerin adını Not'a yaz ve kadans + 3 dk daha bekle.
- Bu kontrolden önce yapılan veriye bağlı kontroller (Pano, anomaliler) bu bekleme dolana kadar "güncel değil" ya da "toplanmadı" gösterebilir; bu doğru durumdur.

**Kaydet:** En uzun kadans, her ailenin son çalışma zamanı (UTC), `doctor` çıkış kodu ve WARN/FAIL kimlikleri.

### 3. Oturum açma

Tarayıcıda `https://<host>/` adresini aç. Dil çerezi yoksa arayüz İngilizcedir (`ba_locale` çerezi yoksa varsayılan `en`); bu bölümün Türkçe etiketleri, dil Türkçe iken görünenlerdir ve ilk kez İngilizce görürsen İngilizce karşılığı yanındadır.

#### MT-12 — Mevcut kullanıcının oturum açması [çekirdek]

**Eylem:**

- `https://<host>/login` sayfasını aç ve var olan yönetici hesabıyla oturum aç. Formun alanları: “Kullanıcı adı” (“Username”) ve “Parola” (“Password”); düğme “Oturum aç” (“Sign in”).

**Beklenen:**

- Sayfa `/` yoluna yönlenir ve “Gösterge paneli” (“Dashboard”) sayfası açılır. Üst çubukta kullanıcı adı görünür.
- “Oturum okunamadı.” (“The session could not be read.”) metni görünmez.
- Yöneticide ilk oturum açmada, konsolun "geçmiş okunsun mu" kararı hiç kaydedilmemişse “QRadar'ın geçmişi okunsun mu?” (“Read QRadar's past?”) penceresi açılır. Burada “Sonra karar ver” (“Decide later”) düğmesini seç; hiçbir şey kaydetme (bölüm 10).

**Kaydet:** Oturum açılabildi mi; ilk açılışta karar penceresi çıktı mı; kullanıcı adı yazılmaz, yalnızca rolü (admin, analyst, viewer…).

#### MT-13 — Hesap menüsü ve API sürümü [çekirdek]

**Eylem:**

- Üst çubukta kullanıcı adına tıkla.

**Beklenen:**

- Menüde “{name} olarak oturum açıldı” satırı, “Kaynak: {source}” satırı (`LOCAL` ya da `LDAP`), “Roller: {roles}” satırı, “Hesap ayarları” (“Account settings”) bağlantısı ve “Oturumu kapat” (“Sign out”) düğmesi vardır. Yerel hesapta “Parolayı değiştir” bağlantısı da vardır.
- Menüde “API sürümü {version}” satırı `API sürümü 0.1.7` gösterir.

**Kaydet:** Menüdeki API sürümü.

#### MT-14 — Hesap sayfası [çekirdek]

**Eylem:**

- `/account` sayfasını aç (menüdeki “Hesap ayarları”).

**Beklenen:**

- Başlık “Hesap ayarları” (“Account settings”). İki bölüm vardır: “Dil” (“Language”) ve “Parola” (“Password”).
- LDAP hesabında parola bölümü “LDAP hesabı parolasını burada değil, dizinde değiştirir.” (“An LDAP account changes its password in the directory, not here.”) metnini gösterir; yerel hesapta parola formu gömülüdür (“Mevcut parola”, “Yeni parola”, “Yeni parolayı doğrula”).

**Kaydet:** Hesabın kaynağı (`LOCAL`/`LDAP`) ve sayfanın açılıp açılmadığı.

#### MT-15 — Oturumu kapatma ve yeniden açma [çekirdek]

**Eylem:**

- Menüden “Oturumu kapat” seç. Sonra aynı hesapla yeniden oturum aç.

**Beklenen:**

- Çıkışta `/login` açılır. Yeniden girişte `/` açılır; dil tercihi korunur.

**Kaydet:** Çıkış ve giriş çalıştı mı.

#### MT-16 — Yeni kullanıcı oluşturma (yazar) yalnızca lab

**Ön koşul:** Yönetici hesabı. **yalnızca lab**: arayüzde kullanıcıyı silme işlemi yoktur (`/admin/users` altında yalnızca roller, devre dışı bırakma, kilit açma ve parola sıfırlama vardır). Test hesabı sonunda yalnızca devre dışı kalır ve listede durur. Üretimde bu kontrolü ve sonraki üçünü `Koşulmadı` işaretle; nedenini Not'a yaz.

**Eylem:**

- `/admin/users` sayfasını aç (“Kullanıcılar”). Form bölümü “Hesap oluştur” (“Create an account”).
- Alanlar: “Kaynak” = “Yerel”; “Kullanıcı adı” = `mt-test-<gün>` (küçük harf, örneğin `mt-test-1101`); “Görünen ad”; “İlk parola” = en az 12 karakterlik bir parola ve kullanıcı adından farklı (bu parolayı kâğıda yazma); rol = “Görüntüleyici (viewer)”. Sonra “Hesap oluştur” düğmesine bas.

**Beklenen:**

- Sonuç satırı: “Yerel kullanıcı {name} oluşturuldu. Hesap, bir sonraki oturum açmada parolayı değiştirmek zorundadır.” Parola bir kez gösterilir: “{name} için ilk parola yalnızca bir kez gösterilir. Hesap, bir sonraki oturum açmada bunu değiştirmek zorundadır.”
- Tabloda yeni satır vardır: kaynak “Yerel”, durum `etkin`. Satırı “Yönet” ile aç: “Parola değişikliği” = “Bir sonraki oturum açmada zorunlu.”

**Kaydet:** Hesap oluştu mu (evet/hayır). Kullanıcı adı ve parola yazılmaz.

**Temizlik:** Bölüm "Test hesabını devre dışı bırak" kontrolü.

#### MT-17 — Zorunlu parola değişikliği (yazar) yalnızca lab

**Ön koşul:** Önceki kontrolde hesap oluştu. İkinci bir tarayıcı penceresi ya da gizli pencere aç; yönetici oturumunu kapatma.

**Eylem:**

- Gizli pencerede `/login` aç ve yeni hesapla, yönetici tarafından yazılan ilk parolayla oturum aç.
- Sayfayı değiştirmeyi dene: adres çubuğuna `/alerts` yaz.
- Parola değiştirme formunda sırasıyla dene: (1) “Mevcut parola” = ilk parola, “Yeni parola” ve “Yeni parolayı doğrula” = 6 karakterlik bir değer; (2) yeni parola ile onay farklı; (3) geçerli (12+ karakter, kullanıcı adından farklı) yeni parola, onay aynı.

**Beklenen:**

- Oturum açıldıktan sonra tarayıcı `/account/password` yoluna gider; başlık “Parolayı değiştir” (“Change password”), açıklama “Parola değiştirilmeden bu hesap devam edemez.” (“This account cannot continue until the password is changed.”). Sağ üstte “Oturumu kapat” düğmesi vardır; menü ve gezinti yoktur.
- `/alerts` yazınca da aynı form gelir (başka sayfa gösterilmez). Backend, bu hesabın diğer isteklerini 403 ile reddeder: “Başka bir istekten önce parolanın değiştirilmesi gerekiyor.”
- (1) “Parola değiştirilmedi.” başlığı altında “Parola en az 12 karakter olmalıdır” satırı; (2) “Yeni parola ile doğrulaması eşleşmiyor.”; (3) “Parola değiştirildi.” ve sayfa `/` yoluna yönlenir. Bundan sonra hesap tüm izinli sayfaları görür.

**Kaydet:** Üç adımın sonucu (evet/hayır). Parolalar yazılmaz.

**Temizlik:** "Test hesabını devre dışı bırak" kontrolü.

#### MT-18 — Görüntüleyici rolünün sınırı (yazar) yalnızca lab

**Ön koşul:** Önceki kontrolde parola değişti; test hesabı `viewer` rolünde ve oturum açık (gizli pencere).

**Eylem:**

- `/admin/users` adresini yaz. Sonra `/admin/directory` adresini yaz. Gezintiye bak.
- `/` sayfasında “Gösterge panelleri” menüsünü aç; “Şablon” grubundan `QRadar Yönetimi (şablon)` girişini seç (şablon kaydedilmez).

**Beklenen:**

- `/admin/users`: “Rolünüz kullanıcıları yönetemiyor (izin: admin:users).” `/admin/directory`: “Rolünüz dizini değiştiremiyor (izin: admin:auth).”
- Gezintide “Kullanıcılar” ve “Dizin” bağlantıları yoktur.
- `QRadar Yönetimi` şablonunda “Kesinti politikası” bileşeni yasak durumunu gösterir ve izni adıyla yazar: “Rolünüz bunu okuyamıyor: {what} (izin: {permission}). Hiçbir şey değiştirilmedi.” (`admin:outage`). Bileşenin bağlantısı yine de sahibi olan rotayı (`/admin/outage-policy`) gösterir. Diğer bileşenler okunur.

**Kaydet:** Üç gözlemin sonucu. Bu, Pano bölümündeki "yasak" durumunun sağlıklı sitede görülebildiği tek yoldur.

**Temizlik:** "Test hesabını devre dışı bırak" kontrolü.

#### MT-19 — Test hesabını devre dışı bırakma (yazar) yalnızca lab

**Ön koşul:** Yönetici oturumu. Gizli pencereyi kapat.

**Eylem:**

- `/admin/users` sayfasında test satırını “Yönet” ile aç ve “{name} hesabını devre dışı bırak” düğmesine bas.

**Beklenen:**

- Sonuç satırı “{name} devre dışı bırakıldı.”; satırın durumu `devre dışı`. Hesapla artık oturum açılamaz.

**Kaydet:** Hesap devre dışı oldu mu.

**Temizlik:** Bu zaten temizliktir. Hesap silinemez; listede devre dışı olarak kalır. Bunu Not alanına yaz.

### 4. Dil

Dil anahtarı **Hesap ayarları** sayfasındadır (`/account`, "Arayüz dili"). Seçim iki yerde saklanır: tarayıcıda `ba_locale` çerezi (yol `/`, bir yıl, `SameSite=Lax`) ve hesapta (`ui_language`). Başka bir tarayıcıda oturum açınca hesaptaki tercih çereze yazılır ve sayfa yenilenir. Çerez ve hesap yoksa arayüz İngilizcedir. Yalnızca İngilizce ve Türkçe vardır.

**Yöntem.** Her rota için dili ayrı ayrı değiştirmek yüzlerce kayıt demektir. Bunun yerine **üç tur** yap: (A) dil İngilizce iken tüm rota kontrollerini aç ve gözle; (B) dili Türkçeye çevir ve aynı rotaları aç; (C) dili İngilizceye geri çevir ve aynı rotaları yeniden aç. Aşağıdaki her satır üç turun sonucunu tek sonuçta toplar. **Tur C bu bölümde yapılmaz:** dil Türkçe kalır ve bölüm 5 ile 15 Türkçe dilde yürütülür; tur C, bölüm 16'dan hemen önce, dili İngilizceye geri çevirince yapılır. Dil değişikliği "dil anahtarı" kontrolündedir.

**Her `Dil:` satırında geçerli kurallar.** Sayfada ham anahtar yoktur; Türkçe turda, aşağıdaki "beklenen İngilizce" listesindekiler dışında İngilizce cümle kalmaz; tur C sayfası tur A ile aynıdır.

**Ham anahtar nedir.** Katalogda karşılığı olmayan bir metin yerine `namespace.anahtar` biçiminde noktalı küçük harfli bir dizi görünür, örneğin `alerts.detail.title` ya da `anomalies.list.metaTitle`. Böyle bir yazı görürsen sayfayı, yazıyı ve dili Not'a yaz; bu Kaldı'dır.

**Eski satırlar.** 0.1.6'nın yazdığı saklı cümleler (anomali açıklamaları, geçiş nedenleri, uyarı başlıkları, kural sağlığı nedenleri…) veritabanında yalnızca İngilizce metin olarak durur; 0.1.7 bunlara kod eklemez. Türkçede de o satırlar İngilizce görünür. Bu doğrudur. Yeni yazılan satırlar (0.1.7'den sonra) Türkçe görünür. Her rota satırının son sütunu eski satırın nerede görüneceğini söyler.

#### MT-20 — Dil anahtarı ve hatırlanması (yazar) [çekirdek]

**Ön koşul:** Başlangıç dilini not et (İngilizce ya da Türkçe).

**Eylem:**

- `/account` sayfasında “Arayüz dili” (“Interface language”) altında “English” ve “Türkçe” seçenekleri vardır. “Türkçe” seç, “Kaydet” düğmesine bas.
- Oturumu kapat, yeniden aç. Başka bir tarayıcıda (ya da çerezleri silerek) oturum aç.

**Beklenen:**

- Kayıttan sonra “Dil tercihi kaydedildi.” (“The language preference was saved.”) görünür ve sayfa Türkçe yenilenir.
- Çıkış ve giriş sonrası arayüz Türkçedir (`ba_locale=tr` çerezi). Başka tarayıcıda da, oturum açıldıktan sonra sayfa Türkçeye geçer (hesap tercihi çereze yazılır).
- Hata durumları: kayıt başarısızsa “Dil tercihi kaydedilemedi. Bağlantınızı denetleyip tekrar deneyin.”; tarayıcı çerezi saklamıyorsa “Tarayıcı çerezi saklamadığı için dil tercihi uygulanamadı.”

**Kaydet:** Başlangıç dili, kayıt iletisi, hatırlama (aynı tarayıcıda / başka tarayıcıda).

**Temizlik:** Testin sonunda dili başlangıç diline geri çevir ve kaydet.

#### MT-21 — Sayı, oran ve zaman biçimi [çekirdek]

**Eylem:**

- Dil Türkçe iken, bin ve üzeri bir sayı gösteren bir sayfa aç. Örnekler: `/alerts` (“Gösterilen uyarı sayısı: {shown} / {total}.”, toplam 1000 ve üzerindeyse), `/log-sources` (“{count} kaynak.”), `/` sayfasındaki EPS kutusu, “Arama” sütunu.
- Bir oran gösteren yer aç (örneğin `/behavior/waf` kaynak tablosu "Endpoint coverage", `/` KPI kutuları). Bir zaman gösteren yer aç (örneğin `/alerts` “Son görülme”).

**Beklenen:**

- Binlik ayırıcı **nokta**: `2.016` (İngilizcede `2,016`). Ondalık ayırıcı **virgül**: `1.260,0` (İngilizcede `1,260.0`).
- Oran, işaret başta ve boşluksuz: `%31,8` (İngilizcede `31.8 %`, işaretten önce bölünmez boşluk). Çok küçük pay `<%0,1`.
- Zaman 24 saatlik ve **UTC** bölgesi her zaman yazılıdır: tam `2026-10-29 12:00:00 UTC`, tablo hücresinde `10-29 12:00 UTC`. Göreli yaş (örneğin `5 dk önce`) mutlak saatin yanındadır, yerine geçmez.

**Kaydet:** Üç biçimden her biri doğru mu; bulunduğu sayfa. Bin altında sayı yoksa `Koşulmadı`.

#### MT-22 — Üst çubuk ve gezinti Türkçe [çekirdek]

**Eylem:**

- Dil Türkçe iken herhangi bir sayfada üst çubuğa ve sol gezintiye bak.

**Beklenen:**

- Gezinti grupları: “Davranış”, “Filo”, “Algılama”, “Operasyon”, “Yönetim”. Öğeler: “Gösterge paneli”, “Genel bakış”, “Anomaliler”, “Kaynaklar”, “WAF davranışı”, “Özellik izlemeleri”, “Filo”, “Filo kaynakları”, “Catch-all gönderenleri”, “Offense'lar”, “Uyarılar”, “Kurallar”, “Kural istatistikleri”, “Özel kurallar”, “Coverage”, “Log source'lar”, “Aramalar”, “Arama sıklığı”, “Sınıflama kuralları”, “Kesinti uyarıları”, “WAF kaynakları”, “IPS kaynakları”, “Geçmiş veri okuma”, “Kullanıcılar”, “Dizin”, “Kılavuz”.
- Üst çubuk: “Açık uyarılar: {count, number}” (açık uyarı sayısı), “Yenileme” (“Kapalı”, “1 dk”, “5 dk”), “Bağlantı durumu: {status}”; arama kutusu “Kaynak, kural ve anomali ara”. `/config-changes` gezintide yoktur; adresle açılır.

**Kaydet:** Eksik ya da İngilizce kalan bir öğe varsa adı.

#### MT-23 — Dil: Giriş sayfası `/login`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Oturum aç”.
- Kodlu alanlar (Türkçe olmalı): Hatalı oturum açma cümlesi (aşağıdaki ayrı kontrol); “API sürümü {version}” satırı.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-24 — Dil: Pano `/` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Gösterge paneli”.
- Kodlu alanlar (Türkçe olmalı): Bileşenler (bölüm 5). Özellik izleme bileşeninin durum nedeni (MSG-4b); son anomaliler ve offense'lar. Üst çubuktaki uyarı sayısı.
- 0.1.6 eski satırı (İngilizce kalması doğru): Son anomaliler tablosunda 0.1.6'dan kalan satırlar.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-25 — Dil: Davranış genel bakışı `/behavior`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Davranış genel bakışı”.
- Kodlu alanlar (Türkçe olmalı): Durum sözcükleri, “Dikkat gerekiyor” tablosu.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-26 — Dil: Anomali listesi `/anomalies` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Anomaliler”.
- Kodlu alanlar (Türkçe olmalı): Filtreler, durum ve algılayıcı adları, konu türleri; satırlarda saklı cümle yoktur.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-27 — Dil: Anomali ayrıntısı, Phase A (log source) `/anomalies/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Anomali incelemesi”.
- Kodlu alanlar (Türkçe olmalı): "Teknik ayrıntılar" sekmesinde “Algılayıcı gerekçesi” cümlesi (MSG-5); “Yaşam döngüsü” sekmesinde her geçişin nedeni (MSG-5).
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce açılmış anomalide ve geçişlerinde İngilizce saklı cümle.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-28 — Dil: Anomali ayrıntısı, WAF endpoint `/anomalies/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Anomali incelemesi”.
- Kodlu alanlar (Türkçe olmalı): Algılayıcı bloğundaki gerekçe cümlesi ve uyarı notu (MSG-6): etiketler “Önem eşiği”, “Doğrulanmış geçmiş”; yaşam döngüsü nedenleri (MSG-5).
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce açılmış WAF anomalisinde İngilizce gerekçe.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-29 — Dil: Anomali ayrıntısı, IPS `/anomalies/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Anomali incelemesi”.
- Kodlu alanlar (Türkçe olmalı): Algılayıcı bloğundaki gerekçe ve uyarı notu Türkçe (MSG-6b); yaşam döngüsü nedenleri (MSG-5). Bastırılmış IPS anomalisi yalnızca "Bastırıldı" etiketini gösterir; bastırma nedeni cümlesi gösterilmez.
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce açılmış IPS anomalisinde İngilizce gerekçe.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-30 — Dil: Anomali ayrıntısı, özellik izleme değeri `/anomalies/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Anomali incelemesi”.
- Kodlu alanlar (Türkçe olmalı): Açıklama, iddia ve geçmiş doğrulama cümleleri (MSG-4b); geçiş nedenleri (MSG-5).
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce açılmış anomalide İngilizce açıklama.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-31 — Dil: Kaynak davranışı `/behavior/sources` ve `/behavior/sources/<id>`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Kaynaklar” / “Kaynak davranışı”.
- Kodlu alanlar (Türkçe olmalı): Durum sözcükleri (“YETERSİZ VERİ”, "öğreniyor"), baseline ve toplama notları.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-32 — Dil: WAF davranışı `/behavior/waf` ve `/behavior/waf/sources/<id>`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “WAF davranışı” / “WAF kaynağı endpoint'leri”.
- Kodlu alanlar (Türkçe olmalı): Toplama durumu sözcükleri; duraklatma ve uygunluk nedenleri (MSG-4a).
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-33 — Dil: Özellik izlemeleri `/behavior/watches`, `/behavior/watches/new`, `/behavior/watches/<id>`

**Ne zaman bakılır:** Yeni satırlar için bölüm 2'deki bekleme.

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Özellik izlemeleri” / “Yeni özellik izleme” / “Özellik izleme”.
- Kodlu alanlar (Türkçe olmalı): “Durum ve neden” sütunu (MSG-4b, her toplamada yeniden yazılır); kapsam nedenleri; geçmiş boşluğu satırları; düzenleyicideki doğrulama nedenleri (MSG-1b/4b).
- 0.1.6 eski satırı (İngilizce kalması doğru): Toplama bir kez çalışana kadar eski durum cümlesi.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-34 — Dil: Filo `/fleet`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Log source filosu”.
- Kodlu alanlar (Türkçe olmalı): Sınıf tablosu, inceleme kuyruğu; saklı cümle yok.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-35 — Dil: Filo kaynakları `/fleet/sources`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Filo kaynakları”.
- Kodlu alanlar (Türkçe olmalı): Sınıflandırma nedenleri (kural adı olarak); çakışma iletileri (MSG-1b) yalnızca hata durumunda.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-36 — Dil: Catch-all gönderenleri `/fleet/catchall`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Catch-all gönderenleri”.
- Kodlu alanlar (Türkçe olmalı): Gönderen tablosu ve gözden geçirme durumu (“incelendi”).
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-37 — Dil: Offense'lar `/offenses` ve `/offenses/<id>`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Offense'lar” / “Offense ayrıntısı”.
- Kodlu alanlar (Türkçe olmalı): Etiketler ve durumlar. Offense açıklaması QRadar'dan gelen metindir; çevrilmez.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-38 — Dil: Uyarılar `/alerts` ve `/alerts/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Uyarılar” / “Uyarı ayrıntısı”.
- Kodlu alanlar (Türkçe olmalı): MSG-7: arama eşiği ve arama hatası, özel kural eşiği, offense toplama güncel değil, yeni log source, yeni catch-all gönderen uyarılarının başlığı, “Açıklama” ve “Çözüm:” satırı Türkçe.
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce açılmış her uyarının başlığı ve açıklaması İngilizce kalır.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-39 — Dil: Kural sağlığı `/rules` ve `/rules/<id>` [çekirdek]

**Ne zaman bakılır:** Yeni satırlar için bölüm 2'deki bekleme.

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Kural sağlığı” / “Kural”.
- Kodlu alanlar (Türkçe olmalı): Kural adının altındaki sağlık nedeni, ayrıntıda “Neden” ve sağlık geçmişi (MSG-3). Her değerlendirmede yeniden yazılır.
- 0.1.6 eski satırı (İngilizce kalması doğru): Sağlık geçmişi tablosundaki 0.1.6 satırları.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-40 — Dil: Kural istatistikleri `/rules/statistics`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Kural istatistikleri”.
- Kodlu alanlar (Türkçe olmalı): Ölçüm penceresi cümlesi, sessiz kuralların dışlama nedeni ve maliyet etiketi ile birimi (MSG-2); hesaplanır, saklı değil.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-41 — Dil: Özel kurallar `/custom-rules`, `/custom-rules/new`, `/custom-rules/<id>`

**Ne zaman bakılır:** Yeni satırlar için bölüm 2'deki bekleme.

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Özel kurallar” / “Yeni özel kural” / “Özel kural”.
- Kodlu alanlar (Türkçe olmalı): “Durum ve neden” sütunundaki durum nedeni (MSG-7, her değerlendirmede yeniden yazılır); kaydetme hataları (MSG-1b).
- 0.1.6 eski satırı (İngilizce kalması doğru): Bir değerlendirme olana kadar eski neden.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-42 — Dil: Algılama coverage'ı `/coverage`

**Ne zaman bakılır:** Yeni satırlar için bölüm 2'deki bekleme.

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Algılama coverage'ı”.
- Kodlu alanlar (Türkçe olmalı): “Kanıt” sütunundaki özet cümleleri (MSG-2); coverage eşitlemesinde yeniden yazılır.
- 0.1.6 eski satırı (İngilizce kalması doğru): Anlık görüntü geçmişi satırları.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-43 — Dil: Log source'lar `/log-sources` ve `/log-sources/<id>`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Log source'lar” / “Log source ayrıntısı”.
- Kodlu alanlar (Türkçe olmalı): Durum sözcükleri; saklı cümle yok.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-44 — Dil: Aramalar `/searches`, `/searches/new`, `/searches/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Arama kataloğu” / “Yeni arama” / “Arama ayrıntısı”.
- Kodlu alanlar (Türkçe olmalı): Çalıştırma geçmişindeki “Hata” cümleleri (MSG-8b); kaydetme hataları (MSG-1b).
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce başarısız olmuş çalıştırmaların hata satırı İngilizce.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-45 — Dil: Yapılandırma değişiklikleri `/config-changes`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Yapılandırma değişiklikleri”.
- Kodlu alanlar (Türkçe olmalı): “Yapılandırma değişiklikleri toplanmıyor.” durumu (toplanmıyor; bu bir "toplanmadı" dürüstlük durumudur).
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-46 — Dil: Yönetim `/admin`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Yönetim”.
- Kodlu alanlar (Türkçe olmalı): Sayfa açıklaması.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-47 — Dil: Arama sıklığı `/admin/search-cadence`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Arama sıklığı”.
- Kodlu alanlar (Türkçe olmalı): Tablo, aile adları, gecikme cümleleri.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-48 — Dil: Sınıflama kuralları `/admin/classification-rules`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Sınıflama kuralları”.
- Kodlu alanlar (Türkçe olmalı): Kural listesi; yerleşik kural çakışma iletileri (MSG-1b) yalnızca hata durumunda.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-49 — Dil: Kesinti uyarıları `/admin/outage-policy`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Kesinti uyarıları”.
- Kodlu alanlar (Türkçe olmalı): Politika tablosu ve not.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-50 — Dil: WAF kaynakları `/admin/waf-sources` ve `/admin/waf-sources/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “WAF kaynakları” / “WAF kaynağı”.
- Kodlu alanlar (Türkçe olmalı): Doğrulama raporu nedenleri, alan ayrıntıları ve uygunluk (duraklatma) nedenleri (MSG-4a); profil çakışmaları (MSG-1b).
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce kaydedilmiş doğrulama raporlarının nedenleri İngilizce.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-51 — Dil: IPS kaynakları `/admin/ips-sources` ve `/admin/ips-sources/<id>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “IPS kaynakları” / “IPS kaynağı”.
- Kodlu alanlar (Türkçe olmalı): Doğrulama raporu nedenleri ve uygunluk nedenleri (MSG-4a).
- 0.1.6 eski satırı (İngilizce kalması doğru): 0.1.7'den önce kaydedilmiş doğrulama raporları.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-52 — Dil: Geçmiş veri okuma `/admin/history-backfill` [çekirdek]

**Ne zaman bakılır:** Yeni satırlar için bölüm 2'deki bekleme.

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Geçmiş veri okuma”.
- Kodlu alanlar (Türkçe olmalı): Kapsam nedenleri (catch-all dahil tüm aileler), boşluk nedenleri, tahmin cümleleri, varsayımlar ve karar iletileri Türkçe (MSG-8a). Kapsam her adımda nedenini yeniden yazar. İstisna: Ariel erteleme nedeni İngilizce kalır (MT-59).
- 0.1.6 eski satırı (İngilizce kalması doğru): Bir sonraki adıma kadar kapsamın eski nedeni (İngilizce).

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-53 — Dil: Kullanıcılar `/admin/users`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Kullanıcılar”.
- Kodlu alanlar (Türkçe olmalı): Tablo ve işlem etiketleri; hesap işlemi hataları (MSG-1).
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-54 — Dil: Dizin `/admin/directory`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Dizin”.
- Kodlu alanlar (Türkçe olmalı): Form etiketleri; doğrulama hataları (MSG-1) yalnızca kayıt denemesinde.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-55 — Dil: Kılavuz `/guide` ve `/guide/<bölüm>` [çekirdek]

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Kılavuz”.
- Kodlu alanlar (Türkçe olmalı): Bölüm başlıkları ve metinleri; bölüm 13.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-56 — Dil: Hesap `/account` ve `/account/password`

**Eylem:**

- Rotayı aç: tur A (dil İngilizce), tur B (dil Türkçe). Tur C, bölüm 16'dadır.

**Beklenen:**

- Türkçe başlık: “Hesap ayarları” / “Parolayı değiştir”.
- Kodlu alanlar (Türkçe olmalı): Parola ilkesi hataları (MSG-1): “Parola en az 12 karakter olmalıdır”.
- 0.1.6 eski satırı (İngilizce kalması doğru): yok.

**Kaydet:** Ham anahtar (sayfa, yazı) ve beklenmeyen İngilizce cümle (sayfa, ilk beş sözcük); yoksa "yok".

#### MT-57 — Hatalı oturum açma cümlesi Türkçe (yazar)

**Ön koşul:** Dil Türkçe. Oturumu kapat.

**Eylem:**

- `/login` sayfasında **var olmayan** bir kullanıcı adıyla (örneğin `mt-yok-kullanici`) ve herhangi bir parolayla oturum açmayı dene. Gerçek bir kullanıcı adı kullanma: hata sayısı hesabı kilitler (5 hata, 15 dakika).

**Beklenen:**

- Sayfa “Kullanıcı adı veya parola geçersiz” gösterir. Dizin ulaşılamazsa “Dizin sunucusuna ulaşılamıyor. Oturum açma tamamlanmadı.” olur.

**Kaydet:** Cümle Türkçe mi.

**Temizlik:** Gerekmez; bir `auth.login_failed` denetim kaydı yazılır.

#### MT-58 — 0.1.6'dan kalan saklı cümleler Türkçede de İngilizcedir

**Eylem:**

- Dil Türkçe iken şu yerlerde 0.1.7'den **önce** yazılmış bir satır bul ve aç: (1) `/anomalies` içinde 0.1.7'den önce çözülmüş bir anomalinin ayrıntısı, "Teknik ayrıntılar" ve "Yaşam döngüsü" sekmeleri; (2) `/alerts` içinde eski bir uyarı; (3) `/rules/<id>` sağlık geçmişinde eski bir satır; (4) `/admin/history-backfill` içinde bir kapsamın nedeni (bir sonraki adıma kadar).

**Beklenen:**

- Bu satırlarda saklı İngilizce cümle aynen görünür; ham anahtar ya da boş alan görünmez. Bu doğrudur: eski satırlara kod eklenmez.
- Aynı sayfalarda 0.1.7'den sonra yazılmış satırlar Türkçedir; bu karışık görünüm normaldir.

**Kaydet:** Dört yerden hangileri için eski satır bulundu; her birinde İngilizce metin okunuyordu (evet/hayır).

#### MT-59 — 0.1.7'de İngilizce kalması beklenenler [çekirdek]

**Eylem:**

- Aşağıdaki listeyi oku. Türkçe turda bu listede **olmayan** bir İngilizce cümle bulursan Kaldı yaz; listedekileri hata sayma:
- **Motorun yazdığı uyarı başlıkları, açıklamaları ve "condition recovered" çözüm nedeni** (Phase A, WAF, IPS ve özellik izleme motorlarının uyarıları): MSG-7b, 0.1.7'de **yok**.
- **Geçmiş okumada Ariel erteleme nedeni** (`deferred: no Ariel permit within N s`; havuz meşgulken bir kapsamın “Neden” hücresi): MSG-8c, 0.1.7'de **yok**; İngilizce kalır ve hata değildir. Catch-all dahil geçmiş okumanın diğer tüm cümleleri MSG-8a ile Türkçedir.
- **İlk açılış sorusundaki tahmin notu** `history.estimate.empty_sources` (henüz izlenen endpoint ya da kabul edilmiş varlığı olmayan bir WAF/IPS kaynağı için, tahmin cümlesinin sonunda "…{count} sources fitted with no … yet; the … limit still bounds the reading"): Türkçe pencerede İngilizce kalır. Geçmiş okuma sayfası bunu göstermez.
- **Eski geçmiş boşlukları ve kapsam satırları** (0.1.7'den önce yazılmış): saklı İngilizce metin.
- Bildirim kanallarına (Teams, e-posta, Slack, webhook, syslog) giden metinler her zaman İngilizcedir; gönderilen bildirim metni arayüz dilini izlemez.
- QRadar'dan, bir sağlayıcıdan ya da bir kütüphaneden gelen serbest metin: `anomaly.error`, Ariel'in kendi hata iletileri (`search_execution.error_message` içinde "Ariel search failed" dışındakiler), izleme filigranının `last_error` değeri.
- Operatörün yazdığı metin: uyarıyı çözerken yazılan neden, arama/kural adları, açıklamalar, sınıflandırma deseni.
- QRadar'dan gelen adlar: offense açıklamaları, log source adları, kural adları, taktik ve teknik adları.
- Hazırlık (readiness) ayrıntıları ve WAF/IPS boru hattı çalışma raporları.
- Çevrilmeyen terimler: `coverage`, `scope`, `baseline`, `offense`, `log source`, `instance`, `custom property`, `cron`, `EPS` gibi sözlük terimleri (D-39) ve ayar adları, izin adları (`read:anomalies`), HTTP kodları.
- 404 "bulunamadı" cümlelerinin sunucu metni (arayüz kendi sabit cümlesini gösterir), kapasite 409 nedenleri, sürüm çakışması iletileri.

**Beklenen:**

- Liste, bu sürümün bilinen sınırıdır.

**Kaydet:** Listede olmayan bir İngilizce cümle gördüysen: sayfa, cümlenin ilk beş sözcüğü. Yoksa `Geçti`.

### 5. Pano (Dashboard)

Pano, `/` yolundadır. Gösterge paneli menüsünden (“Gösterge panelleri”) şablon açılır; şablon kaydedilmez (“Şablon, kaydedilmedi”). Üç şablon kullanılır: “SOC Genel Bakışı”, “QRadar Yönetimi” ve “WAF / IPS İzleme”. Her bileşen kendi veri okumasını yapar; biri başarısız olursa yalnızca o bileşen hata gösterir ve diğerleri çalışır. Her bileşenin bağlantısı, verisinin sahibi olan rotaya gider ve bu bağlantı her durumda (yüklü, boş, güncel değil, yasak, hata) bileşende kalır.

**Sağlıklı sitede görülebilen durumlar.** Yüklü; boş (örneğin açık offense yok); “Toplanmadı” (toplama bir aile için kapalıysa ya da hiç çalışmadıysa: “Toplanmadı.”); “Güncel değil” (“Güncel değil: son veri ‹zaman› tarihli, {age} önce.”; yükseltmeden hemen sonra, ilk kadans dolmadan görülebilir ve doğrudur; kadans + 3 dk sonra sürüyorsa hatadır); yasak (“İzin yok”, yalnızca `viewer` hesabıyla ve `QRadar Yönetimi` şablonunda; Oturum açma bölümündeki "Görüntüleyici rolünün sınırı" kontrolü). **Sağlıklı sitede görülemeyen durum:** "okunamadı" (“Okunamadı: {subject}.”) yalnızca backend ya da bir uç nokta başarısız olursa çıkar; yapılandırma değiştirmeden üretilemez, bu yüzden bu bölümde atlanır. Toplama anahtarı kapalı bırakılmış bir aile için bileşen "Toplanmadı" gösterir; anahtarlar açık olan ailelerde bu durum görülmez. **Bu bölümdeki her bileşen için** yükseltmeden sonra en az bir kadans + 3 dk beklenmiş olmalıdır (MT-11); önce bakarsan "güncel değil" doğrudur.

#### MT-60 — Pano bileşeni: “Toplam EPS”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Başlık “Toplam EPS, {window}”; büyük sayı EPS biriminde; altında “beklenen {eps} EPS” ya da “beklenen — (güvenilir baseline yok)”; “baseline: {have} / {total} kaynak” satırı. Kayıp ölçüm sıfır olarak çizilmez.
- Bağlantı `/fleet` rotasını açar (bağlantı metni “Filo”). Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-61 — Pano bileşeni: “Log source'lar”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Başlık “Log source'lar, güncel durum”; “/ {total} izlenen kaynak”; “Değerlendirilen {judged} kaynak: {normal} normal” ya da “— normal değerlendirildi (henüz değerlendirilen kaynak yok)”; “Sessiz: algılayıcı, olay beklenen yerde hiç olay görmedi (NO_EVENTS anomalisi).”
- Bağlantı `/behavior/sources` rotasını açar (bağlantı metni “Kaynaklar”). Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-62 — Pano bileşeni: “Açık offense'lar”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Başlık “Açık offense'lar, son kesit”; kritik, altında, atanmamış ve SLA aşımı dökümü; büyüklüğü ya da yaşı bilinmeyen offense varsa “+ {count} büyüklüğü bilinmeyen” / “+ {count} yaşı bilinmeyen”. Kesit hiç toplanmadıysa “Henüz offense kesiti toplanmadı.”
- Bağlantı `/offenses?status=OPEN` rotasını açar (bağlantı metni “Offense'lar”). Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-63 — Pano bileşeni: “Etkin anomaliler”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Başlık “Etkin anomaliler, güncel durum”; “Algılayıcıya göre” ve “Yaşam döngüsü” dökümü.
- Bağlantı `/anomalies?active_only=true` rotasını açar (bağlantı metni “Anomaliler”). Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-64 — Pano bileşeni: “Filo EPS”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Eksende birim (EPS) yazılıdır; beklenen aralık bant olarak çizilir; eksik zaman dilimi boşluktur, sıfır değildir. Yükleme sırasında “{what} yükleniyor…” biçimli metin görünür.
- Bağlantı `/fleet` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-65 — Pano bileşeni: “Kaynak sınıfına göre EPS”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Sınıf adları “Güvenlik duvarı”, “Windows” biçiminde Türkçe; tamamlanmamış sınıf için “Bu aralıkta tamamlanmış zaman dilimi olan sınıf yok.” nedeni.
- Bağlantı `/fleet` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-66 — Pano bileşeni: “EPS'e göre en yoğun log source'lar”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. EPS'e göre sıralı kaynak satırları; sayılar sağa yaslı, Türkçe gruplamalı.
- Bağlantı `/behavior/sources` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-67 — Pano bileşeni: “Önem derecesine göre anomaliler”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Önem derecesine göre sayılar: “Kritik”, “Yüksek”, “Orta”, “Düşük”, “Bilgi”.
- Bağlantı `/anomalies` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-68 — Pano bileşeni: “Log source sağlığı”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Kaynak sağlığı sayıları; “YETERSİZ VERİ” ayrı sayılır ve "normal" içine katılmaz.
- Bağlantı `/behavior/sources` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-69 — Pano bileşeni: “Son anomaliler”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Son anomali satırları; zaman `UTC` bölgesiyle. 0.1.6'dan kalan satırlar varsa onlar da listelenir.
- Bağlantı `/anomalies` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-70 — Pano bileşeni: “Son offense'lar”

**Eylem:**

- “SOC Genel Bakışı” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Son offense satırları; açıklama QRadar'dan geldiği gibi.
- Bağlantı `/offenses` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-71 — Pano bileşeni: “Zamanlanmış aramalar”

**Eylem:**

- “QRadar Yönetimi” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Her aile için kadans, son çalışma ve bir sonraki çalışma; hiç çalışmamış aile “Henüz çalışmadı” gösterir. Bu bileşen "zamanlanmış aramalar" için tek ekrandır.
- Bağlantı `/admin/search-cadence` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-72 — Pano bileşeni: “Algılama sağlığı”

**Eylem:**

- “QRadar Yönetimi” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Kural ve coverage özeti; değerlendirilmemişse “Coverage değerlendirilmedi.” Bu bir sıfır coverage bulgusu değildir.
- Bağlantı `/coverage` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-73 — Pano bileşeni: “Kanıt toplama”

**Eylem:**

- “QRadar Yönetimi” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Kanıt toplama durumu sayıları (bekliyor, başarısız, kısmi); başarısız sayısı bağlantıyla açılan filtreyi besler.
- Bağlantı `/anomalies?evidence_status=FAILED` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-74 — Pano bileşeni: “İncelenecek catch-all gönderenleri”

**Eylem:**

- “QRadar Yönetimi” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. İncelenecek gönderenler; yoksa boş durum metni.
- Bağlantı `/fleet/catchall` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-75 — Pano bileşeni: “Kaynak sınıfları”

**Eylem:**

- “QRadar Yönetimi” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Kaynak sınıfı tablosu; sınıf adları Türkçe.
- Bağlantı `/fleet` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-76 — Pano bileşeni: “Kesinti politikası”

**Eylem:**

- “QRadar Yönetimi” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Sınıf başına kesinti uyarı politikası. Yönetici hesabında yüklenir; `viewer` hesabında yasak durumu görünür (Oturum açma bölümündeki kontrol).
- Bağlantı `/admin/outage-policy` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-77 — Pano bileşeni: “WAF kaynakları”

**Eylem:**

- “WAF / IPS İzleme” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. WAF kaynakları; son zaman dilimi coverage'ı ve endpoint sayısı üst sınırla; toplanmayan kaynak “Devre dışı” ya da “Hiçbir zaman dilimi toplanmadı” gösterir. Sitede WAF yoksa “Kayıtlı WAF kaynağı yok. Bir kaynak, WAF profili oluştuğunda burada görünür.”
- Bağlantı `/behavior/waf` rotasını açar (bağlantı metni “WAF”). Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-78 — Pano bileşeni: “Son WAF anomalileri”

**Eylem:**

- “WAF / IPS İzleme” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Etkin WAF anomalileri; yoksa boş metin. Anomali yoksa `Koşulmadı` işaretleme: boş durum da geçerli bir sonuçtur.
- Bağlantı `/anomalies?subject_kind=WAF_ENDPOINT&active_only=true` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

#### MT-79 — Pano bileşeni: “Son IPS anomalileri”

**Eylem:**

- “WAF / IPS İzleme” şablonunda bileşeni bul, verisini oku, bağlantısına bas.

**Beklenen:**

- Veri yüklenir. Etkin IPS anomalileri; bağlantı IPS varlıklarını ve kaynaklarını birlikte açar (0.1.6 ile gelen "IPS varlıkları ve kaynakları" seçeneği). Satırdaki cümleler Türkçedir (MSG-6b).
- Bağlantı `/anomalies?subject_kind=IPS&active_only=true` rotasını açar. Zamanlar UTC bölgesiyle yazılıdır; "Son veri" satırı (“Son veri: ‹zaman›.”) vardır.

**Kaydet:** Veri yüklendi mi; bağlantı doğru rotayı açtı mı. "Güncel değil" ise bekleme süresinin dolup dolmadığını Not'a yaz.

### 6. Anomaliler

Anomali listesi tek yerde dört aileyi toplar: log source (Phase A), WAF endpoint, IPS (varlık ve kaynak) ve özellik izleme değeri. Ayrıntı sayfası (`/anomalies/<id>`) aileye göre farklı bir inceleme açar. Yaşam döngüsü durumları: “ADAY”, “AÇIK”, “DÜZELİYOR”, “ÇÖZÜLDÜ”, “BASTIRILDI”; “YETERSİZ VERİ” ve “NORMAL” kaynak durumudur. Sitede bir tür anomali yoksa o türün kontrolü `Koşulmadı` olur ve Not'a "veri yok" yazılır; yeni anomali üretmeye çalışma.

#### MT-80 — Anomali listesi ve filtreler

**Eylem:**

- `/anomalies` sayfasını aç. Filtreleri sırayla dene: “Konu türü”, “Yaşam döngüsü durumu”, “Yalnızca etkin” (“Filtrele”).

**Beklenen:**

- Başlık “Anomaliler”. Sütunlar: “Konu”, “Algılayıcı”, “Durum”, “Gözlenen → Beklenen”, “Sapma”, “Önem derecesi”, “Başlangıç”, “Kanıt”.
- Konu türü seçenekleri: “Log source'lar”, “WAF endpoint'leri”, “IPS varlıkları”, “IPS kaynakları”, “IPS varlıkları ve kaynakları”, “Özellik izleme değerleri”. “IPS varlıkları ve kaynakları” seçeneği IPS varlıklarını ve kaynaklarını birlikte listeler.
- Filtre sonrası etkin filtre çipleri (“Filtre:”) görünür; eşleşme yoksa “Bu filtrelerle eşleşen anomali yok. Bu filtrelenmiş bir görünümdür, filo hakkında bir açıklama değildir.” Hiç anomali yoksa “Hiç anomali algılanmadı. Yeterli baseline'ı olmayan kaynaklar değerlendirilmiyor — kaç tane olduğunu davranış genel bakışından denetleyin.” Saatler UTC; durum sözcükleri Türkçe.

**Kaydet:** Beş filtre ve sonuç sayfası çalıştı mı; liste satır sayısı (yaklaşık).

#### MT-81 — Phase A anomali ayrıntısı ve dürüstlük durumları

**Ön koşul:** Sitede en az bir Phase A anomalisi var (etkin ya da çözülmüş).

**Eylem:**

- Listeden bir log source anomalisini aç. Sekmeleri sırayla aç: “Kanıt”, “Yaşam döngüsü”, “Teknik ayrıntılar”.

**Beklenen:**

- Başlık “Anomali incelemesi”; "Ne değişti" (“Ne değişti”) ve “Vaka ölçümleri”: “Gözlenen”, “Beklenen”, “Sapma”, “Süre”; zaman çizelgesi “Zaman çizelgesi”.
- Kanıt durumuna göre bir başlık görünür: “Katkı sağlayan kanıtı hâlâ toplanıyor. Aşağıdaki analiz henüz mevcut değil: bu bir birikimdir, bir bulgu değildir.” / “Bazı QRadar alanları kullanılamıyordu veya kırpıldı. Mevcut kanıt aşağıda gösteriliyor; toplanmayan boyutlar kontrol edilmedi ve oradaki katkı sağlayanların yokluğundan hiçbir sonuç çıkmaz.” / “Bu log source'un DSM'i bu aralık için istenen alanların hiçbirini sunmuyor; bu nedenle katkı sağlayan analizi yapılamadı. Bu, geçici bir hata değil, kaynağın bir özelliğidir.” / “Katkı sağlayan kanıtı toplama başarısız oldu. Algılama metrikleri ve yaşam döngüsü verileri kullanılabilir durumda; bu, anomali sırasında ne olduğu hakkında hiçbir şey söylemez — yeniden denemeye değer.” / “Bu anomali için kanıt toplama istenmedi. Hiçbir şeye bakılmadı.” metinlerinden biri (yalnız ilgili durumda); kırpılmış sonuçta “Kırpıldı.” ile başlayan metin. Bunlar "yetersiz", "kısmi", "kırpılmış", "toplanmadı" dürüstlük durumlarıdır; sıfır olarak çizilmez.
- Bir kanıt başarısız ise “Toplama hatası.” ve sağlayıcı hata metni görünür; bu metin serbest metindir ve Türkçe olmaz (beklenen İngilizce listesi).

**Kaydet:** Anomalinin durumu, kanıt durumu başlığı varsa hangisi, üç sekme açıldı mı. Bu kontrol için sitede anomali yoksa `Koşulmadı`.

#### MT-82 — Yaşam döngüsü sekmesi ve nedenler

**Ön koşul:** Phase A ya da başka bir ailenin bir anomalisi açık.

**Ne zaman bakılır:** Yeni geçiş, ancak yükseltmeden sonra durum değişen bir anomaliyle görülür; beklenmesi gerekmez.

**Eylem:**

- Ayrıntıda “Yaşam döngüsü” sekmesini aç.

**Beklenen:**

- Her geçişin satırında önceki ve sonraki durum, zaman (UTC) ve neden bulunur. İlk geçiş “ilk” olarak yazılır. Nedeni kaydedilmemiş geçiş “Neden kaydedilmedi” gösterir.
- 0.1.7'den **sonra** yazılmış bir geçişin nedeni Türkçedir (MSG-5: dört ailenin tümü); 0.1.7'den önceki geçişin nedeni İngilizce saklı metindir. Bu kontrol için yeni geçiş yoksa yalnızca eski satırın İngilizce okunduğunu doğrula ve Not'a "yeni geçiş yok" yaz.

**Kaydet:** Sekme açıldı mı; neden metni var mı; en yeni geçişin yazılma saati ve dili.

#### MT-83 — WAF anomali ayrıntısı

**Ön koşul:** Sitede bir WAF anomalisi var.

**Eylem:**

- Listeden bir WAF endpoint anomalisini aç. Algılayıcı bloğunu ve “Yaşam döngüsü” bölümünü oku.

**Beklenen:**

- Blokta “Tespit anındaki mevsimsel p05–p95” ya da “Tespit anındaki beklenen aralık”, “Önem eşiği” (“önemli kabul edilir: ≥ {count} istek”), “Doğrulanmış geçmiş”, “Art arda anormal zaman dilimi” satırları vardır. Ölçülmeyen bir değer tire ve gerekçe ile gösterilir (ör. “kullanılmadı: MAD sıfırdı”); sıfır çizilmez.
- Gerekçe cümlesi ve varsa uyarı notu (ör. engelleme oranı, yeni endpoint) Türkçedir (MSG-6). 0.1.7'den önce açılmış anomalide bu cümleler saklı İngilizce metindir.
- Kaynak bağlantısı “WAF kaynağını görüntüle”.

**Kaydet:** Bir gerekçe cümlesinin dili (Türkçe/İngilizce) ve anomalinin açılış zamanı.

#### MT-84 — IPS anomali ayrıntısı

**Ön koşul:** Sitede bir IPS anomalisi var (Cisco Firepower/FTD ya da Palo Alto Threat kaynağından).

**Eylem:**

- Listeden bir IPS varlığı ya da IPS kaynağı anomalisini aç. Algılayıcı bloğunu, “Zaman çizelgesi”, “Değerlendirilenler” ve “Yaşam döngüsü” bölümlerini oku.

**Beklenen:**

- Blokta “Tespit anında sezonluk p05–p95”, “Robust z eşiği”, “Önemlilik eşiği”, “Doğrulanmış geçmiş” satırları vardır. Ölçülmeyen değer tire ve gerekçe taşır. Kanıt bölümü “Bir IPS vakasının kanıt paketi ve katkı sağlayan değerler listesi yoktur. Yukarıdaki seri ve değerlendirilenler algılayıcının kaydettikleridir; IPS için kaynak adresleri sunulmaz.” der: IPS için kaynak adresi gösterilmez.
- Gerekçe cümlesi Türkçedir (MSG-6b): varlık ani artışı (“{observed} {unit}, {subject} için baseline medyanı olan {expected} değerinin üzerinde ({ratio_text}, +{delta} {unit}/zaman dilimi); {basis}”), engelleme oranı kayması, yoğunlaşma, yeni hedef port (“İlk göründüğü {first_seen} tarihinden bu yana {port} hedef portu {buckets} zaman diliminde {events} olay taşıdı; doğrulanmış {history_start} – {history_end} geçmişinde her zaman diliminde {admission} olayın altında kaldı”; üç UTC zaman damgalı cümle). Uyarı notu sabit cümledir, örneğin “Engelleme, profilin eylem eşlemesi üzerinden değerlendirilir. Daha yüksek bir engelleme oranı IPS kararlarını tanımlar; bir saldırının başarılı olduğunu göstermez.” Sayım bir alt sınırsa cümle “En az {observed} {unit} (alt sınır: {scope}), {subject} için baseline medyanı olan {expected} değerinin üzerinde ({ratio_text}, +{delta} {unit}/zaman dilimi); {basis}” biçimindedir. Bin ve üzeri sayılar Türkçe gruplanır (`12.345`), oranlar `%60,0` biçimindedir.
- İç içe cümle, iki nokta üst üste ya da tireden sonra küçük harfle başlar (örneğin "alt sınır: …", "…, {role}: {reason}"); büyük harfle başlayan iç cümle hatadır. 0.1.7'den önce açılmış anomalide gerekçe ve not saklı İngilizce metindir.
- Bastırılmış (bakım penceresindeki) IPS anomalisi yalnızca “BASTIRILDI” etiketini gösterir. Bastırma nedeni cümlesi saklanır ama ne API'den sunulur ne de bir sayfada gösterilir: bu cümleyi bekleme, görmemek hata değildir.
- Yaşam döngüsü nedenleri Türkçedir (MSG-5).

**Kaydet:** Bir gerekçe cümlesinin dili ve anomalinin açılış zamanı. IPS anomalisi yoksa `Koşulmadı`.

#### MT-85 — Özellik izleme anomali ayrıntısı

**Ön koşul:** Sitede bir özellik izleme anomalisi var.

**Eylem:**

- Listeden bir “Yeni değer” ya da “Değer ani artışı” anomalisini aç.

**Beklenen:**

- Açıklama ve gerekçe cümleleri Türkçedir (MSG-4b), yaşam döngüsü nedenleri Türkçedir (MSG-5); eski satırlar İngilizcedir. Yeni değer anomalisinde "iddia" ve geçmiş doğrulama cümleleri bulunur (ISO zaman damgalarıyla).

**Kaydet:** Bir cümlenin dili ve anomalinin açılış zamanı. Özellik izleme anomalisi yoksa `Koşulmadı`.

#### MT-86 — Dürüstlük durumları görünür ve sıfıra çevrilmez

**Eylem:**

- Şu yerlere bak: (1) `/behavior/sources` içinde yeterli baseline'ı olmayan bir kaynak; (2) `/behavior/waf` ya da `/admin/waf-sources` içinde toplanmayan bir kaynak; (3) `/config-changes`; (4) `/offenses` içinde kesit hiç toplanmamışsa.

**Beklenen:**

- (1) Kaynak “YETERSİZ VERİ” durumunu ve “öğreniyor” ibaresini gösterir; sayılarda "normal" içine girmez (“Değerlendirilmiyor — yukarıdaki sıfır bunları kapsamaz.”).
- (2) Toplanmayan kaynak “Toplanmadı” ya da “hiçbir zaman dilimi toplanmadı” gösterir; sıfır olay yazmaz.
- (3) “Yapılandırma değişiklikleri toplanmıyor.” ve “Bunlar için bir veri modeli var, ancak onu dolduran bir toplayıcı ya da API yok; bu yüzden bu sayfada gösterilecek bir şey yok.”: toplanmayan veri için sıfır ya da boş liste çizilmez.
- (4) “Henüz offense kesiti toplanmadı.” Ayrıca kısmi ve kırpılmış ölçümler “Kısmi” ve “Kırpılmış” etiketlidir.

**Kaydet:** (1)-(4) için gördüğün durum; görünmeyen için `Koşulmadı`.

### 7. Alarmlar (Uyarılar)

Arayüzde bu sayfanın adı **Uyarılar**'dır (İngilizce: Alerts), rota `/alerts`. Yaşam döngüsü: açık → alındı → çözüldü; çözülen bir uyarı yeniden açılamaz.

#### MT-87 — Uyarı listesi

**Eylem:**

- `/alerts` sayfasını aç. Durum filtresini dene (“Uyarı durumu”).

**Beklenen:**

- Başlık “Uyarılar”; sütunlar: “Başlık”, “Önem derecesi”, “Durum”, “Oluşumlar”, “İlk görülme”, “Son görülme”. Durum filtresi: “Açık”, “Alındı”, “Çözüldü”.
- Hiç uyarı yoksa “Hiç uyarı oluşturulmadı.” Sayım satırı: “Gösterilen uyarı sayısı: {shown} / {total}.”
- 0.1.7'den sonra açılan arama, özel kural, offense toplama, yeni log source ve yeni catch-all gönderen uyarılarının başlığı Türkçedir (MSG-7); eski uyarıların başlığı İngilizcedir. Motor uyarıları (Phase A, WAF, IPS, özellik izleme) 0.1.7'de de İngilizcedir (MSG-7b yok).

**Kaydet:** Listenin açıldığı, satır sayısı ve bir başlığın dili.

#### MT-88 — Uyarı ayrıntısı: Açıklama ve çözüm nedeni

**Ön koşul:** Sitede en az bir uyarı var.

**Eylem:**

- Listeden bir uyarıyı aç.

**Beklenen:**

- Başlık “Uyarı ayrıntısı”. "Açıklama" kartı (“Açıklama”) uyarının açıklamasını taşır. “Kanıt”, “Yaşam döngüsü” bölümleri ve “Bildirim teslim geçmişi” tablosu vardır; bildirim yoksa “Bu uyarı için bildirim yok.”
- Çözülmüş uyarıda “Çözüm:” satırı çözüm nedenini gösterir; 0.1.7'den sonra yazılan sistem nedenleri Türkçedir (ör. “Koşul düzeldi”, “Offense toplama güncelliği düzeldi.”); bir operatörün yazdığı neden olduğu gibidir. Eski uyarının nedeni İngilizcedir (ör. "condition recovered").

**Kaydet:** Açıklama kartının göründüğü, bir çözüm nedeninin metni ve dili. Çözülmüş uyarı yoksa o kısım için `Koşulmadı`.

#### MT-89 — Uyarıyı alındı yapma ve çözme (yazar) yalnızca lab

**Ön koşul:** **yalnızca lab**: alındı ya da çözüldü işaretlenen bir uyarı, analistlerin gördüğü durumu değiştirir ve çözülen uyarı yeniden açılamaz; üretimde bu kontrolü `Koşulmadı` işaretle. Labda, test için oluşmuş bir uyarı üzerinde yap.

**Eylem:**

- Ayrıntıda “Alındı olarak işaretle” düğmesine bas. Sonra “Çöz” seç, “Çözüm nedeni” alanına bir neden yaz, “Bu uyarı çözülsün mü? Çözülen bir uyarı yeniden açılamaz.” metnini oku ve onayla.

**Beklenen:**

- “Uyarı alındı olarak işaretlendi.”; durum “ALINDI”. Çözünce “Uyarı çözüldü.”; durum “ÇÖZÜLDÜ”; “Bu uyarı çözüldü. Başka işlem yapılamaz.” Yazılan neden aynen “Çözüm:” satırında görünür.

**Kaydet:** İki adımın sonucu.

**Temizlik:** Geri alınamaz (çözüm). Labda uyarıyı test amacıyla seç; sonra silme yoktur. MT-1b bunu lab VM'de yapar.

### 8. Özel kurallar

Özel kural, saklanan toplamlar üzerinde eşik kuralıdır; QRadar'a hiçbir şey göndermez. `/custom-rules` rotası. Kural oluşturmak için `custom_rule:write` (yönetici ya da `detection_engineer`) gerekir; `read:custom_rules` yalnızca okutur.

#### MT-90 — Özel kural listesi ve durumları

**Ne zaman bakılır:** Yeni neden cümlesi, yükseltmeden sonra bir 5 dakikalık değerlendirme (bir kadans + 3 dk) sonra Türkçe görünür.

**Eylem:**

- `/custom-rules` sayfasını aç.

**Beklenen:**

- Başlık “Özel kurallar”; sütunlar: “Ad”, “Durum ve neden”, “Konu”, “Koşul”, “Önem derecesi”, “Etkinlik”, “Son değerlendirme (UTC)”.
- Durum sözcükleri: “Uygun”, “Açık”, “Düzeliyor”, “Karar yok”, “Güncel değil”, “Toplanmadı”, “Konu kullanımdan kaldırıldı”, “Devre dışı”, “Etkin değil”; hiç değerlendirilmemiş kural “Hiç değerlendirilmedi”. Durumun yanındaki neden cümlesi Türkçedir (MSG-7, her değerlendirmede yeniden yazılır).
- Değerlendirme anahtarı kapalıysa “CUSTOM_RULES_ENABLED false olduğu için kural değerlendirmesi kapalıdır. Etkin kurallar değerlendirilmiyor.”; uyarı gönderimi kapalıysa “CUSTOM_RULES_ALERTING_ENABLED açılana kadar ihlaller kaydedilir ancak bildirim gönderilmez.” Kural yoksa “Henüz özel kural yok.”

**Kaydet:** Liste açıldı mı; bir kuralın durumu ve neden cümlesinin dili. Kural yoksa `Koşulmadı`.

#### MT-91 — Özel kural ayrıntısı

**Ön koşul:** En az bir özel kural var.

**Eylem:**

- Listeden bir kuralı aç.

**Beklenen:**

- Başlık “Özel kural”; “Geçerli durum”, “Konu”, “Koşul”, “Son değerlendirme” ve “İhlal geçmişi” bölümleri vardır. İhlal yoksa “Bu kural için kaydedilmiş etkin ihlal yok.” Etkin kural ayrıca “Uyarı modu” ya da “Gölge modu” etiketini taşır.

**Kaydet:** Ayrıntı sayfası açıldı mı.

#### MT-92 — Özel kural taslağı önizlemesi (kaydetmeden)

**Ön koşul:** Hesapta `custom_rule:write` var (yönetici).

**Eylem:**

- `/custom-rules/new` sayfasını aç (“Yeni özel kural”). Konu türü olarak “Filo sınıfı”, bir filo sınıfı ve bir metrik seç; “Eşik ({unit})” gir. **Kaydetme.**

**Beklenen:**

- Alt bölümde “Taslak önizlemesi” açılır: “Bu taslağı saklanan toplamlar üzerinde yeniden oynatır. Hiçbir şeyi saklamaz ve QRadar'a hiçbir şey göndermez.” Önizleme saklanan zaman dilimlerinden çizilir; eksik zaman dilimi “Ölçülmedi” ve boşluk olarak gösterilir, sıfır çizilmez. Eksiksizlik ve ölçüm türü etiketleri: “Kesin” / “Alt sınır” / “Ölçülmedi”.

**Kaydet:** Önizleme çizildi mi.

**Temizlik:** Sayfadan kaydetmeden çık: hiçbir şey saklanmaz.

#### MT-93 — Özel kural oluşturma ve silme (yazar)

**Ön koşul:** Hesapta `custom_rule:write` var. Kural **devre dışı** kaydedilir; test bitince silinir.

**Eylem:**

- `/custom-rules/new` sayfasında: “Ad” = `MT-test-kural`; konu = bir filo sınıfı; bir metrik ve eşik; “Etkin” **işaretsiz**; “Kural oluştur” düğmesine bas.
- Kuralın ayrıntı sayfasında “Sil” seç; “Bu kural silinsin mi? Açık bir uyarısı varsa kural ve ihlal geçmişi kaldırılmadan önce uyarı çözülür.” metnini oku ve “Kuralı sil” ile onayla.

**Beklenen:**

- Kayıt sonrası “Kural kaydedildi.”; kural listede “Devre dışı” durumundadır ve uyarı üretmez (değerlendirme anahtarı `CUSTOM_RULES_ENABLED` kapalıysa durum “Etkin değil” olur).
- Silme sonrası kural listede yoktur.

**Kaydet:** Oluşturma ve silme çalıştı mı. Kural adı sayfaya yazılmaz.

**Temizlik:** İkinci adım zaten temizliktir. Denetim kaydında `custom_rule.create` ve `custom_rule.delete` satırları kalır.

### 9. Aramalar

Arama kataloğu, saklanan ve doğrulanmış AQL aramalarını içerir (`/searches`). Arayüz ham AQL çalıştırmaz. Bir aramanın QRadar'a gitmesi Ariel kapasitesi harcar; bu bölümde QRadar'a arama gönderen kontrol **yalnızca lab**'dır.

#### MT-94 — Arama listesi

**Eylem:**

- `/searches` sayfasını aç.

**Beklenen:**

- Başlık “Arama kataloğu”; sütunlar: “Ad”, “Kategori”, “MITRE”, “Zamanlama”, “Önem derecesi”, “Sürüm”, “Etkin”. Arama yoksa “Henüz tanımlanmış zamanlanmış arama yok.” Yazma izni olmayan hesapta “Arama oluşturmak için search:write izni gerekir. Bir yönetici veya detection_engineer rolü bu izni verebilir.” ya da “Düzenleyici gizli. ”.

**Kaydet:** Liste açıldı mı; satır sayısı.

#### MT-95 — Arama çalıştırma geçmişi ve hata satırı [çekirdek]

**Ön koşul:** En az bir arama zamanlanmış ve çalışmış.

**Ne zaman bakılır:** Bir zamanlanmış çalıştırma, aramanın cron kadansı kadar bekleme gerektirir.

**Eylem:**

- Bir aramayı aç (`/searches/<id>`). “Çalıştırma geçmişi” tablosunu oku.

**Beklenen:**

- Başlık “Arama ayrıntısı”; tablo sütunları: “Başlangıç (UTC)”, “Durum”, “Tetikleyici”, “Sürüm”, “Süre”, “Sonuç”, “Yeniden deneme”, “Eşik”, “Hata”. Durumlar: “TAMAMLANDI”, “BAŞARISIZ”, “ZAMAN AŞIMI”; tetikleyici “Zamanlanmış” ya da “Elle”.
- Kırpılmış çalıştırmada sayı alt sınırdır (`≥` işaretiyle; arama satır sınırında durmuştur) ve eşik sütunu “karar yok” yazar.
- Başarısız bir satırın “Hata” hücresi: 0.1.7'den sonra yazılan sabit hata iletileri Türkçedir (MSG-8b): “Ariel araması başarısız oldu”, “Arama, {timeout} sn zaman aşımını aştı”, “{waited} sn içinde Ariel arama izni alınamadı (konsol başına üst sınır {instance_cap}, genel üst sınır {global_cap})”. Ariel'in kendi iletisi ve istisna metni serbest metindir (İngilizce kalır). Eski satırlar "Ariel search failed" biçiminde İngilizcedir. Başarısız satır yoksa `Koşulmadı`.

**Kaydet:** Tablo açıldı mı; bir başarısız satırın hata iletisi ve dili (varsa).

#### MT-96 — Zamanlama tabanı (cron) ve önayarlar

**Ön koşul:** Hesapta `search:write` var (yönetici).

**Eylem:**

- Bir aramanın “Zamanlama” bölümünde zamanlama listesini aç (“Şuna değiştir”). **Kaydetme.**

**Beklenen:**

- Liste yedi önayar sunar: 5 dk, 15 dk, 30 dk, 1 sa, 2 sa, 4 sa, 8 sa (cron `*/5 * * * *` … `0 */8 * * *`). Hiçbiri 5 dakikadan kısa değildir.
- Taban **300 saniyedir** (`CRON_FLOOR_SECONDS`). 300 sn'den sık tetiklenen bir `schedule_cron` API tarafından 422 ile reddedilir; ileti MSG-8b ile Türkçe de kodludur: “schedule_cron {cron} ({timezone}) {gap} sn arayla tetikleniyor ({previous}, ardından {next}); bir arama en az {min_seconds} sn arayla tetiklenmelidir”. Arayüz yalnızca önayar sunduğundan bu reddi arayüzden tetikleyemezsin; ret otomatik testlerle kanıtlıdır ve burada elle denenmez.
- Özel bir cron'u olan arama varsa listede “Özel (mevcut kalsın)” ve cron olduğu gibi gösterilir.

**Kaydet:** Önayar listesinin yedi seçeneği doğru mu. Reddin kendisi denenmedi (bunu Not'a yaz).

**Temizlik:** Sayfadan kaydetmeden çık.

#### MT-97 — Aramayı şimdi çalıştırma (yazar) yalnızca lab QRadar araması

**Ön koşul:** **yalnızca lab**: manuel çalıştırma QRadar'a bir Ariel araması gönderir (konsol kapasitesi harcar), silinemez bir "Elle" çalıştırma satırı bırakır ve eşiği aşarsa uyarı açar ya da uyarıyı kapatır. Üretimde `Koşulmadı` işaretle. Labda **eşiği olmayan** tek bir arama seç ve yalnız bir kez çalıştır.

**Eylem:**

- Aramanın ayrıntısında “Şimdi çalıştır” düğmesine bas (“Çalışıyor…”).

**Beklenen:**

- Çalıştırma geçmişinin en üstüne tetikleyici “Elle” olan yeni bir satır eklenir; durum önce “ÇALIŞIYOR”, sonra “TAMAMLANDI”.
- Ariel havuzu doluysa çalıştırma başlamaz ve 503 ile şu cümle görünür: “Ariel arama bütçesi dolu (eşzamanlı çalışan: {held}); {waited} sn beklendi; QRadar'a hiçbir şey gönderilmedi” (MSG-8b). Başka bir hatada “Çalıştırma başlamadı. Sayfayı yenileyip tekrar deneyin.”; yetki yoksa “Rolünüz saklanan bir aramayı çalıştıramıyor (izin: search:execute).”

**Kaydet:** Yeni satır geldi mi; sonuç sayısı. Bir aramadan fazlasını çalıştırma.

**Temizlik:** Satır silinemez. Notta saati yaz.

#### MT-98 — Yeni arama saklama (yazar) yalnızca lab

**Ön koşul:** **yalnızca lab**: arayüzde arama silme yoktur; saklanan arama devre dışı da olsa katalogda kalır ve analistlerin gördüğü listeye girer.

**Eylem:**

- `/searches/new` sayfasını aç (“Yeni arama”). Bir ad, AQL ve zamanlama yaz; “Devre dışı olarak kaydet” düğmesine bas.

**Beklenen:**

- Arama devre dışı kaydedilir ("Yeni arama devre dışı olarak kaydedilir" — “Yeni arama devre dışı olarak kaydedilir. Sonucu önizlemek için kendi sayfasından çalıştırın; yalnızca kaydedilmiş sorgu çalışır.”). Geçersiz AQL'de “Doğrulama nedenleri” başlığı altında Türkçe nedenler görünür (MSG-1b).

**Kaydet:** Kaydedildi mi; geçersiz AQL nedenleri Türkçe mi.

**Temizlik:** Silinemez; aramayı devre dışı bırak.

### 10. Geçmiş okuma

Sayfa `/admin/history-backfill` yolundadır (“Geçmiş veri okuma”); okumak için `read:history_backfill`, kararı değiştirmek için `admin:history_backfill` gerekir. Geçmiş okuma yalnızca öğrenme içindir; okunan kovalar yargılanmaz. `HISTORY_BACKFILL_ENABLED` varsayılanı `true`'dur. Okuma, QRadar'a salt okunur arama gönderir ve 72 saatlik bütçe içinde kalır (`docs/OPERATIONS.md`, "Geçmiş okuma").

#### MT-99 — Geçmiş okuma sayfası: karar bloğu ve tablo

**Ön koşul:** Yönetici hesabı.

**Ne zaman bakılır:** Kapsam satırları, ilgili ailenin bir sonraki toplamasında güncellenir; bir kadans + 3 dk.

**Eylem:**

- `/admin/history-backfill` sayfasını aç.

**Beklenen:**

- Başlık “Geçmiş veri okuma”. Her etkin console için “Console {name}” bölümü: karar satırı (“Geçmiş veri okuma: Evet{days}. Kaydeden: {by}, ‹zaman›.”, “Geçmiş veri okuma: Hayır, geçmiş okunmuyor. Kaydeden: {by}, ‹zaman›.” ya da “Geçmiş veri okuma: karar verilmedi. QRadar'ın geçmişinden hiçbir şey okunmuyor.”) ve tablo.
- Tablo sütunları: “Aile”, “Scope”, “Durum”, “İstenen pencere”, “Kanıtlanan pencere”, “Okunan”, “Arama”, “Okunan son zaman”, “Neden”, “Sonradan Evet denirse”. Aileler: “Kaynak hacmi”, “WAF”, “IPS”, “Catch-all gönderenleri”, “Kural eşleşmeleri”.
- Durum sözcükleri: “Bekliyor”, “Okunuyor”, “Tamamlandı”, “Kısmi”, “Kullanılamıyor”, “Uygun değil”. Okunmayan sayı tire olarak görünür, sıfır olarak değil.
- “Neden” sütunundaki cümleler her ailede (catch-all dahil) Türkçedir (MSG-8a); cümle parçaları küçük harfle başlar ve noktalı virgülle birleşir. Örnekler: “tarama pencerenin sonuna ulaştı”; “okuma sona ulaştı; taban, istenen başlangıca bir zaman diliminden az uzaklıkta ve boşluk yok”; “sona ulaşıldı; {problems}” (iç parçalar: “QRadar, {cut} / {total} için istenenden azını tutuyor”, “{sources} için pencerede olay yoktu”); “pencere {asked} günden {days} güne indirildi: {limits}”; “arama bütçesi bekleniyor”; boşluk satırı “Arama en küçük genişlikte {failures} kez başarısız oldu”; catch-all: “pencerede hiçbir catch-all kaynağının olayı yoktu; hiçbir şey yazılmadı”. Sayılar Türkçe gruplanır, süre "yaklaşık X saat" biçimindedir. “Sonradan Evet denirse” sütunu: “mümkün”, “olanaksız: bu kaynak zaten veri topladı”, “gerekmez: bu kaynağın geçmiş kapsamı var”.
- İstisnalar (İngilizce kalır, hata değildir): kapsam Ariel havuzu meşgul olduğu için ertelendiyse nedeni `deferred: no Ariel permit within N s` (MT-59); 0.1.7'den önce yazılmış satırlar ve boşluklar saklı İngilizce metindir. Kapsam her adımda nedenini yeniden yazar, bu yüzden bir adımdan sonra Türkçeye döner.
- Console yoksa “Etkin bir QRadar console'u yok.”; yetki yoksa “Rolünüz bunu okuyamıyor (izin: read:history_backfill). Hiçbir şey değiştirilmedi.”

**Kaydet:** Sayfa açıldı mı; kararın durumu (Evet / Hayır / karar verilmedi); aile durumlarının özeti; bir neden cümlesinin dili.

#### MT-100 — Geçmiş okuma: ilk açılış sorusu

**Ön koşul:** Konsolun kararı hiç kaydedilmemişse (bölüm 3'te pencere açıldıysa).

**Eylem:**

- Soru penceresinde “Sonra karar ver” düğmesini seç. Sayfa üstündeki şeridi oku.

**Beklenen:**

- Pencere kapanır, hiçbir karar kaydedilmez ve sayfada “Geçmiş veri okuma kararı verilmedi. Karar verilene kadar QRadar'ın geçmişinden hiçbir şey okunmaz. Geçmiş veri okumayı aç” şeridi görünür. Pencere hiç açılmadıysa karar zaten kayıtlıdır: bu kontrol `Koşulmadı`.

**Kaydet:** Pencere çıktı mı; "Sonra karar ver" seçildi mi.

#### MT-101 — Geçmişi şimdi oku / Okumayı durdur (yazar) yalnızca lab QRadar araması

**Ön koşul:** **yalnızca lab**: bu düğmeler her ailenin sonraki toplamasında QRadar'a salt okunur aramalar gönderir (kapasite harcar) ve okumayı başlatır ya da durdurur; üretimde `Koşulmadı` işaretle.

**Eylem:**

- Karar bloğundaki “Geçmişi şimdi oku” düğmesine bas, ““{action}” eylemi onaylansın mı?” penceresinde onayla. Sonra aynı yerde “Okumayı durdur” ile durdur.

**Beklenen:**

- Kaydedildi: “Kaydedildi. ”. Satırlar hemen değişmez; her aile sonraki toplamasında uygular. Durdurmada okuyan kapsamlar “Kısmi” biter ve nedeni durduranın adıyla yazılır (“durduran: {by}”); "Geçmişi şimdi oku" sonrası eksikleri tamamlayan kapsam “geçmişi şimdi oku: eksikler tamamlanıyor” yazar. Kayıt iletileri: ““Geçmişi şimdi oku” kaydedildi. Her aile bunu sonraki toplamasında uygular; henüz hiçbir scope değişmedi.” / ““Okumayı durdur” kaydedildi. Her aile okuyan scope'larını sonraki toplamasında sonlandırır; henüz hiçbir scope değişmedi.” Yetkisiz hesapta “Değiştirilmedi. Rolünüz geçmiş veri okumayı değiştiremiyor (izin: {permission}).”

**Kaydet:** İki adımın bildirimi; sonraki kadansta satırın durumu.

**Temizlik:** Bir sonraki adım olarak "Geçmişi şimdi oku" okumayı yeniden başlatır; 'Evet' kararı hiçbir zaman geri alınamaz, okuma durdurulabilir.

### 11. Kural sağlığı, istatistikler, coverage ve filo

Bu sayfaların hepsi QRadar'dan toplanmış veriyi okur; hiçbirinde yazma işlemi yoktur.

#### MT-102 — Kural sağlığı listesi

**Ne zaman bakılır:** Kural sağlığı değerlendirmesi kural eşitlemesiyle çalışır; bir kadans + 3 dk.

**Eylem:**

- `/rules` sayfasını aç. Sağlık filtresini dene (“Sağlık durumuna göre filtrele”).

**Beklenen:**

- Başlık “Kural sağlığı”; sütunlar: “Kural”, “Tür”, “Etkinlik”, “Sağlık”, “Anlam”, “Son tetiklenme (UTC)”, “Offense'lar”. Sağlık sözcükleri: “Sağlıklı”, “Hiç gözlenmedi”, “Etkin değil”, “Gürültülü”, “Devre dışı”, “Bağımlılık bozulmuş”, “Yetersiz veri”, “Bilinmiyor”.
- Sağlıklı ya da bilinmeyen olmayan her kuralın adının altında kararın nedeni yazılıdır; neden kaydedilmemişse “Kaydedilmiş neden yok”. 0.1.7'de neden cümlesi Türkçedir (MSG-3); her kural değerlendirmesinde yeniden yazıldığı için ilk değerlendirmeden sonra tüm satırlarda Türkçe görünür.
- Kararı olmayan kural varsa “Henüz sağlık kararı olmayan kural sayısı: {count}.” uyarısı çıkar; bu bir hata değildir.

**Kaydet:** Liste açıldı mı; bir nedenin dili.

#### MT-103 — Kural ayrıntısı ve sağlık geçmişi

**Ön koşul:** En az bir kural toplanmış.

**Eylem:**

- Listeden bir kuralı aç (`/rules/<id>`).

**Beklenen:**

- Başlık “Kural”; bölümler: “Sağlık”, “Sağlık kanıtı”, “Meta veriler”, “MITRE teknikleri”, “Sağlık geçmişi”. Sağlık geçmişi tablosunda her satırın “Neden” hücresi vardır; eski satırlar İngilizce saklı metindir, yeniler Türkçedir.
- Kapasite geçmişi bölümü (“Kapasite geçmişi”) ölçüt yoksa “QRadar, toplama başladığından beri bu kuralı yeniden ölçütlemedi.” der; bu bir hata değildir.

**Kaydet:** Sayfa açıldı mı; bir geçmiş satırının nedeni ve dili.

#### MT-104 — Kural istatistikleri

**Eylem:**

- `/rules/statistics` sayfasını aç. Gün seçimini (bir değer) değiştir (“Ölçüm günleri”).

**Beklenen:**

- Başlık “Kural istatistikleri”. Bölümler: “Kural bileşimi”, “En çok eşleşenler”, “Sessiz etkin kurallar”, “Ölçüte göre en maliyetli”, “Kapasite geçmişi”. Ölçüm penceresi tam ise “Eksiksiz.”, kısmi ise “KISMİ.”, yetersiz ise “YETERSİZ VERİ.” yazılır; eksik pencere sessizlik kanıtı sayılmaz (“Sessizlik değerlendirilmedi.”).
- Maliyet birimi: “Etkin kurallar, değerlendirmesi en maliyetli olan en düşük kapasite ölçütünden başlayarak sıralanır. Birim: {unit}.” Sessiz kural dışlama nedeni ve ölçüm penceresi cümleleri Türkçedir (MSG-2). Bir sayı sıfır olarak çizilmez: ölçülemeyen bölüm “Kural eşleşmelerini ölçen eksiksiz pencere yok.” gibi bir başlık gösterir.
- QRadar sistemi kayıtlı değilse “Kayıtlı QRadar sistemi yok.”

**Kaydet:** Sayfa açıldı mı; ölçüm penceresinin durumu (tam/kısmi/yetersiz).

#### MT-105 — Coverage

**Eylem:**

- `/coverage` sayfasını aç.

**Beklenen:**

- Başlık “Algılama coverage'ı”. Eşleme yoksa “Coverage değerlendirilmedi.” ve “Hiçbir ATT&CK teknik eşlemesi yapılandırılmamış; bu yüzden coverage ölçülemez. Eşlemeleri bir yönetici ekler. Bu, sıfır coverage bulgusu değildir.” (bu bir sıfır coverage bulgusu değildir). Eşleme varsa “{total} teknik değerlendirildi · {covered} kapsandı · {explicit} açık / {inferred} çıkarımsal eşleme · oluşturulma: ‹zaman›” özeti ve “Teknikler” tablosu: “Teknik”, “Taktik”, “Durum”, “Kurallar”, “Puan”, “Kanıt”. Durum sözcükleri: “KAPSANDI”, “KISMİ”, “BOZULMUŞ”, “EKSİK”, “DEĞERLENDİRİLMEDİ”.
- “Kanıt” hücrelerindeki özet cümleleri Türkçedir (MSG-2) ve coverage eşitlemesinde yeniden yazılır; eski anlık görüntüler İngilizce kalır.

**Kaydet:** Sayfa açıldı mı; bir kanıt cümlesinin dili.

#### MT-106 — Filo, filo kaynakları ve catch-all gönderenleri

**Eylem:**

- Sırayla aç: `/fleet`, `/fleet/sources`, `/fleet/catchall`.

**Beklenen:**

- `/fleet`: “Log source filosu”; sınıf tablosu ve “İnceleme kuyruğu”; pencere “Son {window}:  ile  arası, {minutes} dakikalık zaman dilimlerinde.” Eksik pencere kısmi/eksik zaman dilimi sayısıyla yazılır (“Olay sayısı ve ortalama EPS yalnızca tamamlanmış zaman dilimlerini sayar. Pencere eksikse satır, kaç zaman diliminin kısmi ya da eksik olduğunu söyler.”).
- `/fleet/sources`: “Filo kaynakları”; yeni ve sınıfı belirsiz kaynaklar ve verilen sınıf/neden.
- `/fleet/catchall`: “Catch-all gönderenleri”; envanterde catch-all kaynağı yoksa “Envanterde catch-all kaynağı yok.”

**Kaydet:** Üç sayfa açıldı mı.

#### MT-107 — Log source'lar ve ayrıntı

**Eylem:**

- `/log-sources` sayfasını aç, bir kaynağı aç.

**Beklenen:**

- Başlık “Log source'lar”; “{count} kaynak.”; sütunlar: “Ad”, “Tür”, “Kritiklik”, “Durum”, “Anomaliler”, “Son olay”. Ayrıntı başlığı “Log source ayrıntısı”; “Son olay: ‹zaman›”; hiç olay yoksa “Son olay: hiç”.

**Kaydet:** Sayfalar açıldı mı; kaynak sayısı.

#### MT-108 — Yapılandırma değişiklikleri

**Eylem:**

- `/config-changes` adresini yaz (gezintide yoktur).

**Beklenen:**

- Başlık “Yapılandırma değişiklikleri”; “Yapılandırma değişiklikleri toplanmıyor.” ve “Bunlar için bir veri modeli var, ancak onu dolduran bir toplayıcı ya da API yok; bu yüzden bu sayfada gösterilecek bir şey yok.” Bu, "toplanmadı" dürüstlük durumudur: veri toplanmaz, bu yüzden boş liste ya da sıfır gösterilmez.

**Kaydet:** Sayfa bu metni gösteriyor mu.

#### MT-109 — Davranış sayfaları: genel bakış, kaynaklar, WAF, özellik izlemeleri, offense'lar

**Ne zaman bakılır:** Özellik izleme durum nedeni, izlemenin bir sonraki toplamasında Türkçe olur; bir kadans + 3 dk.

**Eylem:**

- Sırayla aç: `/behavior`, `/behavior/sources` (bir kaynağı aç), `/behavior/waf` (bir kaynağın endpoint sayfasını aç), `/behavior/watches` (bir izlemeyi aç), `/offenses` (bir offense'ı aç).

**Beklenen:**

- `/behavior`: “Davranış genel bakışı”; üst istatistikler “Açık anomaliler”, “Sessiz kaynaklar”, “Yetersiz veri” (“Değerlendirilmiyor — yukarıdaki sıfır bunları kapsamaz.”).
- `/behavior/sources`: “Kaynaklar”; kaynak ayrıntısı “Kaynak davranışı”; öğrenen kaynak “öğreniyor”.
- `/behavior/waf`: “WAF davranışı”; WAF kaynak sayfası “WAF kaynağı endpoint'leri”; toplanmayan kaynak “Toplanmadı”. Duraklatma gerekçeleri (MSG-4a) Türkçedir.
- `/behavior/watches`: “Özellik izlemeleri”; sütunlar “Durum ve neden”, “Scope”, “Açık vakalar”. Toplama kapalıysa “TOPLAMA KAPALI.”, gölge moddaysa “GÖLGE MODU.” Durum nedenleri Türkçedir (MSG-4b) ve her toplamada yeniden yazılır; geçmiş okuyan izlemede geçmiş durumu (“Geçmiş okunuyor”) görünür.
- `/offenses`: “Offense'lar”; offense yoksa “Henüz offense kesiti toplanmadı.” ya da “QRadar'dan henüz offense toplanmadığı için bu liste boş. Offense toplama işlemini bir yönetici ayarlar.”; salt okunur ("Bu sayfa hiçbir yazma işlemi yapmaz").

**Kaydet:** Beş sayfa açıldı mı; izleme yoksa o kısım `Koşulmadı`.

### 12. Yönetim

Bu bölümde sayfalar önce **yalnızca görüntülenir**. Her değişiklik **(yazar)** kontrolüdür ve geri alması yazılıdır; üretimde değişiklik kontrolleri `yalnızca lab`'dır. Hiçbir yönetim sayfası QRadar'a yazmaz. Arama sıklığı, kesinti politikası ve sınıflama kuralı sayfaları üretimde yalnızca okunur.

#### MT-110 — WAF kaynakları: liste ve profil

**Eylem:**

- `/admin/waf-sources` sayfasını aç; bir kaynağın profil sayfasını aç (`/admin/waf-sources/<id>`). **Doğrulamayı başlatma, hiçbir şey kaydetme.**

**Beklenen:**

- Liste başlığı “WAF kaynakları”; sütunlar: “Kaynak”, “Ürün”, “Eşleme”, “Doğrulama”, “Toplama”, “Son doğrulama (UTC)”. Doğrulama etiketleri: “GEÇERLİ”, “ESKİ”, “GEÇERSİZ”, “SONUÇSUZ”, “DOĞRULANMAMIŞ”. Hiç doğrulanmamış kaynak “hiç doğrulanmadı” gösterir (tire, başarısız doğrulama değildir).
- Duraklatılmış bir kaynağın toplama hücresi neden cümlesi taşır (ör. “Profil sürümü {profile_version} hiç doğrulanmadı”, “WAF toplaması devre dışı (WAF_COLLECTION_ENABLED=false)”); bu cümleler MSG-4a ile Türkçedir. Profil sayfasında “Alan eşlemesi”, “Doğrulama”, “Toplama”, “Geçmiş” bölümleri vardır.
- WAF profili yoksa “Hiçbir log source WAF kaynağı olarak yapılandırılmadı.”

**Kaydet:** Liste ve profil açıldı mı; bir duraklatma nedeninin dili (varsa).

#### MT-111 — IPS kaynakları: liste ve profil

**Ön koşul:** Üretim IPS kaynakları: Cisco Firepower/FTD ve Palo Alto Threat.

**Eylem:**

- `/admin/ips-sources` sayfasını aç; bir kaynağın profilini aç (`/admin/ips-sources/<id>`). **Doğrulamayı başlatma, hiçbir şey kaydetme.**

**Beklenen:**

- Başlık “IPS kaynakları”; sütunlar: “Kaynak”, “Profil”, “Doğrulama”, “Etkin”, “Toplama”, “Son doğrulama (UTC)”. Profil sayfası başlığı “IPS kaynağı”; bölümler: “Alan eşleme”, “Doğrulama”, “Toplama”, “Geçmiş”.
- Doğrulama raporunun nedenleri ve alan ayrıntıları (ör. “QRadar bu alanı bu adla kabul etmiyor.”, “Alan mevcut ama penceredeki hiçbir olay kullanılabilir bir değer taşımıyor.”) ile uygunluk nedenleri Türkçedir (MSG-4a). Profil yoksa “Henüz hiçbir IPS kaynağının profili yok.” IPS işlemleri için `admin:ips` gerekir (`admin:waf` vermez).

**Kaydet:** Liste ve profil açıldı mı; bir nedenin dili.

#### MT-112 — Kaynak doğrulamasını yeniden çalıştırma (yazar) yalnızca lab QRadar araması

**Ön koşul:** **yalnızca lab**: “Yeniden doğrula” doğrulamayı yeniden çalıştırır, QRadar'a salt okunur yoklama aramaları gönderir ve profilin doğrulama sonucunu değiştirir; bu, bir kaynağın toplanmasını durdurabilir. Üretimde `Koşulmadı` işaretle.

**Eylem:**

- Labda bir WAF ya da IPS profilinde “Yeniden doğrula” düğmesine bas.

**Beklenen:**

- “Sürüm {version} için doğrulama istendi.”; kısa süre sonra doğrulama sonucu ve neden cümleleri güncellenir (Türkçe).

**Kaydet:** Sonuç etiketi.

**Temizlik:** Önceki sonuç korunur ya da yenisiyle değişir; geri alınamaz. Labda sorun değildir.

#### MT-113 — Kullanıcılar ve dizin (yalnızca görüntüleme)

**Eylem:**

- `/admin/users` ve `/admin/directory` sayfalarını aç. **Dizin için bağlantı testini çalıştırma** (gerçek bir parola gerekir) ve hiçbir şey kaydetme.

**Beklenen:**

- `/admin/users`: “Kullanıcılar”; sütunlar “Kullanıcı adı”, “Kaynak”, “Roller”, “Durum”, “Son oturum açma (UTC)”. Saatler UTC.
- `/admin/directory`: “Dizin”; form “Dizin yapılandırması”; “Bağlantıyı test et” bölümü: “Kullanıcı adı ve parola yukarıdaki formla birlikte gönderilir, sonra temizlenir. Kaydedilmez.”

**Kaydet:** İki sayfa açıldı mı.

#### MT-114 — Arama sıklığı (yalnızca görüntüleme)

**Eylem:**

- `/admin/search-cadence` sayfasını aç.

**Beklenen:**

- Başlık “Arama sıklığı”; sütunlar: “Aile”, “Sıklık”, “Zaman dilimi”, “En kötü durum algılama gecikmesi”, “Son çalıştırma (UTC)”, “Sonraki çalıştırma (UTC)”, “Son çalıştırmadaki arama”. Sekiz aile listelenir (ör. “Phase A hacmi”, “WAF toplama”, “IPS toplama”, “Kural eşleşmeleri”). Hiç çalışmamış aile için tire ya da “Yükseltmeden beri çalışmadı”/“sayılmadı”; bunlar sıfır değildir.

**Kaydet:** Sekiz ailenin her birinin kadansı (5 dk, 15 dk…).

#### MT-115 — Arama sıklığını değiştirme (yazar) yalnızca lab

**Ön koşul:** **yalnızca lab**: sıklık, QRadar'a ne sıklıkla arama gönderileceğini değiştirir; üretimde `Koşulmadı`.

**Eylem:**

- Bir ailenin sıklığını bir ön ayara değiştir ve kaydet; sonra eski değere geri al.

**Beklenen:**

- “Kaydedildi. {user} tarafından ‹zaman› zamanında değiştirildi.” ya da “Kaydedildi. ‹zaman› zamanında değiştirildi.” iletisi; yetkisizse “Rolünüz arama sıklığını değiştiremiyor (izin: admin:search_cadence).”

**Kaydet:** Değişiklik kaydedildi mi, geri alındı mı.

**Temizlik:** Eski değere geri al; denetim kaydında iki satır kalır.

#### MT-116 — Kesinti politikası ve sınıflama kuralları (yalnızca görüntüleme)

**Eylem:**

- `/admin/outage-policy` ve `/admin/classification-rules` sayfalarını aç.

**Beklenen:**

- `/admin/outage-policy`: “Kesinti uyarıları”; "Yalnızca kayıt" ve uyaran sınıflar. `/admin/classification-rules`: “Sınıflama kuralları”; yerleşik kurallar değiştirilemez, yalnız açılıp kapatılabilir (ileti: “Yerleşik bir kural yalnızca açılıp kapatılabilir”).

**Kaydet:** İki sayfa açıldı mı.

#### MT-117 — Yönetim ana sayfası

**Eylem:**

- `/admin` adresini yaz (gezintide yoktur).

**Beklenen:**

- Başlık “Yönetim”; açıklama “Toplamayı, kaynak sınıflamasını ve kesinti uyarılarını denetleyen ayarlar.”

**Kaydet:** Sayfa açıldı mı.

### 13. Kılavuz

Uygulama içi kılavuz `/guide` yolundadır; oturum açan herkese açıktır ve izin istemez. Metni sunucuda üretilir ve dile göre gelir.

#### MT-118 — Kılavuz iki dilde açılır

**Eylem:**

- Dil Türkçe iken `/guide` sayfasını aç, bir bölümü aç (`/guide/how-it-works`). İngilizce yarısını dil İngilizceye döndüğünde, bölüm 16'daki "Dil, tur C" sırasında yap.

**Beklenen:**

- Türkçe: başlık “Kılavuz”, açıklama “Konuyu yanıtladığı soruya göre seçin.”, gruplar “Kavramlar”, “Analist sayfaları”, “Analist görevleri”, “Yönetici görevleri”; ilk bölüm “Nasıl çalışır”.
- İngilizce (tur C'de): başlık “Guide”, gruplar “Concepts”, “Analyst pages”; ilk bölüm “How it works”. Her iki dilde bölüm sayfası boş değildir ve ham anahtar yoktur.

**Kaydet:** Her iki dilde iki sayfa açıldı mı.

### 14. QRadar'a karşı salt okunur

Bu araçta hiçbir şey QRadar'a yazmaz (`docs/OPERATIONS.md` dört kuraldan dördüncüsü). Sağlayıcı arayüzünde (`app/providers/base.py`) QRadar yapılandırması için oluşturma, güncelleme ya da silme yöntemi yoktur; yalnızca Ariel arama yaşam döngüsü yöntemleri vardır (`create_ariel_search`, `get_ariel_search_status`, `get_ariel_search_results`, `cancel_ariel_search`). REST sağlayıcının QRadar'a yaptığı istekler kodda şunlardır: `GET /system/about`, `GET /config/event_sources/log_source_management/log_sources`, `…/log_source_types`, `…/log_source_groups`, `GET /config/event_sources/custom_properties/…`, `GET /analytics/rules`, `GET /analytics/building_blocks`, `GET /siem/offenses`, `GET /ariel/searches/<id>` ve `…/results`, ayrıca Ariel arama kaydı için `POST /ariel/searches` ve kendi aramasını iptal için `DELETE /ariel/searches/<id>`. Son ikisi QRadar'da geçici bir arama nesnesi oluşturur ve siler; yapılandırma nesnesi (kural, log source, özel özellik, offense, referans veri) oluşturmaz, değiştirmez, silmez.

Bu depoda QRadar konsolunun denetim günlüğünün yeri belgelenmemiştir; o yüzden konsol tarafı kontrolü **konsol yöneticisinin adımıdır**.

#### MT-119 — `doctor` QRadar'a tek çağrı yaptığını gösterir [çekirdek]

**Eylem:**


```bash
./qradar-dash doctor | grep -E 'qradar_tls|qradar_auth'
```


**Beklenen:**

- `qradar_auth` satırı `GET /system/about succeeded; QRadar <sürüm>` der (salt okunur bir `GET`). `qradar_tls` PASS ise `verified certificate expires in <n> days` der. `doctor` Ariel araması çalıştırmaz ve QRadar'a yazmaz.

**Kaydet:** İki satırın durumu ve QRadar sürümü.

#### MT-120 — Konsol tarafı: test penceresinde yazma işlemi olmadığının doğrulanması konsol yöneticisi adımı [çekirdek]

**Ön koşul:** Konsol yöneticisi test başlangıç ve bitiş saatlerini (MT-05) biliyor. Bu kontrolün yeri bu depoda belgelenmemiştir; yönetici kendi konsolunun denetim/erişim kayıtlarını kullanır. Ulaşılamıyorsa `Koşulmadı`.

**Eylem:**

- Konsol yöneticisinden, uygulamanın API kullanıcısı (yetkili hizmet/belirteç) için test penceresi boyunca konsoldaki işlemleri incelemesini iste.

**Beklenen:**

- Penceredeki istekler yalnızca yukarıda sayılan okuma (`GET`) uç noktalarındadır; yazma olarak yalnızca `POST /ariel/searches` ve `DELETE /ariel/searches/<id>` görülür.
- Kural, yapı taşı, log source, log source grubu, özel özellik, referans veri kümesi, offense (not, atama, kapatma, gizleme), kullanıcı ya da yetkili hizmet üzerinde `POST`, `PUT`, `PATCH` ya da `DELETE` görülmez.
- Uygulamanın API kullanıcısının izin kümesi okuma (ve Ariel) ile sınırlıysa bu ek bir güvencedir.

**Kaydet:** Yöneticinin cevabı: yazma yok / şu işlem görüldü. Konsol kayıtları sayfaya yazılmaz.

### 15. Geri alma ölçütleri ve adımları

**Hemen geri al** (bölüm 15'teki adımlar):

- `upgrade` sıfırdan farklı çıktı ve otomatik geri alma de siteyi eski hâline getiremedi (`Rollback failed; the writers and the UI are stopped. Verified backup: <yol>`). OPS-R45.
- Yükseltme başarılı görünüyor ama `doctor` yükseltme sonrası bir `FAIL` veriyor ve `docs/OPERATIONS.md` içindeki ilgili OPS-R girişi onu temizlemiyor (özellikle `db_connect`, `migration_head`, `encryption`, `secrets_files`, `watermarks`).
- Oturum açma bozuk: yönetici ve mevcut kullanıcılar giremiyor, `/api/v1/health/ready` 200 değil.
- İlk kadanstan + 3 dk sonra, `/admin/search-cadence` içindeki hiçbir aile çalışmıyor ve `doctor` `celery_beat` ya da `celery_workers` FAIL veriyor.
- 0.1.6'nın gösterdiği veri yok: anomaliler, uyarılar, kurallar, log source'lar ya da offense'lar boş ya da eksik (ve sebebi "toplanmadı" dürüstlük durumu değil).

**Not al ve devam et:**

- Bölüm 4'teki "beklenen İngilizce" listesinde olmayan tek tük bir İngilizce cümle ya da ham anahtar: ilgili kontrol Kaldı yazılır ve teslim edilir, ama bu bir geri alma nedeni değildir.
- Küçük görsel kusurlar: bir hücrede kesilme, hizalama.
- Bir bileşenin "güncel değil" göstermesi, bekleme süresi (kadans + 3 dk) dolmadan.
- Hazır olmayan veriler (WAF/IPS anomalisi yok) nedeniyle `Koşulmadı` olan kontroller.

**Önemli:** Bir şey Kaldı olunca, geri almadan **önce** `./qradar-dash support-bundle` ve `./qradar-dash logs --tail=2000 backend` kaydet. Geri alma, yükseltmeden sonra yazılan veriyi bozuk kopyada bırakır (`qradar_obs_broken_<utc>`). Alembic downgrade hiçbir zaman kullanılmaz; geri alma bir yedeğin geri yüklenmesidir.

#### MT-121 — Geri alma ölçütlerinin değerlendirilmesi [çekirdek]

**Eylem:**

- Yukarıdaki "Hemen geri al" listesine bak ve test boyunca bu maddelerden biri gözlendi mi yaz.

**Beklenen:**

- Hiçbiri gözlenmedi.

**Kaydet:** "Hemen geri al" maddelerinden gözlenen var mı (evet/hayır); evet ise hangisi ve karar.

#### MT-122 — Geri alma (yalnızca gerekirse) (yazar)

**Ön koşul:** Yukarıdaki ölçütlerden biri gözlendi ve karar verildi. Gerek yoksa `Koşulmadı`. Destek paketi alındı.

**Eylem:**


```bash
./qradar-dash support-bundle
./qradar-dash rollback
```

- Komut `Rollback replaces the live database with <yol>; data written since is kept only in the broken copy.` yazar ve `Type rollback to continue: ` bekler; `rollback` yaz.
- Bittikten sonra checkout'u çalışan sürüme döndür ve doğrula:

```bash
git checkout v0.1.6
./qradar-dash status
curl -s http://127.0.0.1:8000/api/v1/version
./qradar-dash doctor
```


**Beklenen:**

- `Rollback complete.` satırı; çıkış kodu 0; `PASS  migration_head database and image are at 0032` (bir kez).
- `status`: `Installed version: 0.1.6`, `Last upgrade: rolled_back, …`; `/api/v1/version`: `"version":"0.1.6"` ve `"migration_head":"0032"`. Oturum açma çalışır. `doctor`: `FAIL` yok.
- Betik bozuk veritabanı kopyasının adını (`qradar_obs_broken_<utc>`) ve onu daha sonra silmek için tam komutu yazar; **silmeyi** acele etme.
- Geri alma kendi sağlık denetiminde başarısız olursa yazıcılar durur ve `Rollback failed; the writers and the UI are stopped. Verified backup: <yol>` yazılır (OPS-R45); `./qradar-dash rollback` komutunu yinele ya da `./qradar-dash restore <yol>` kullan.

**Kaydet:** Komutun çıkış kodu, son satır, sonrasındaki `Installed version` ve `migration_head` değerleri.

**Temizlik:** Yoktur; geri alma geri almadır.

#### MT-123 — Geri alma provası (yazar) yalnızca lab

**Ön koşul:** **yalnızca lab**: bu, üretim sitesinde yapılmaz.

**Eylem:**

- Lab VM'de yükseltmeden sonra yukarıdaki geri alma adımlarını çalıştır, sonra yeniden `upgrade 0.1.7` ile yükselt.

**Beklenen:**

- Geri alma sonrası baş `0032`, ikinci yükseltme sonrası `0044`; `Last upgrade:` satırı en son işlemi gösterir.

**Kaydet:** İki sonuç.

**Temizlik:** Provada bozuk kopya kalır; labda silinebilir (betiğin yazdığı komut).

### 16. Sonuçların teslimi

Sonuç tablosu `docs/MANUAL-TEST-RESULTS.md` dosyasıdır; basılı kâğıda ya da dosyanın bir kopyasına doldurulur.

#### MT-124 — Dil, tur C: İngilizceye dönüş (yazar) [çekirdek]

**Eylem:**

- Dili başlangıç diline (İngilizce) çevir (`/account`, "Arayüz dili") ve bölüm 4'teki `Dil:` satırlarının rotalarını yeniden aç (üretimde yalnızca `[çekirdek]` işaretli olanlar).

**Beklenen:**

- Her rotada sayfa, tur A'daki ile aynıdır: İngilizce başlıklar, saklı İngilizce cümleler olduğu gibi, ham anahtar yok, sayılar `2,016` biçiminde (virgülle gruplu), zaman `UTC` bölgesiyle.

**Kaydet:** Her `Dil:` satırı için tur C'nin sonucu `Dil:` satırının Not alanına yazılır; sapma yoksa satırın sonucu tur A ve tur B'ye göre verilir.

**Temizlik:** Dil başlangıç diline dönmüş olur.

#### MT-125 — Sonuç sayfasını doldur ve teslim et [çekirdek]

**Eylem:**

- Sayfanın üst bilgisini ve her satırı doldur: `Geçti`, `Kaldı` ya da `Koşulmadı`; `Koşulmadı` ve `Kaldı` için Not yaz. Bir kontrol Kaldı ise ilgili destek paketini ve `logs --tail=2000 backend` çıktısını ekle.

**Beklenen:**

- Sayfada boş sonuç hücresi yoktur. Şunlar **hiçbir yere yazılmaz**: parolalar, belirteçler, `.env` değerleri, `ENCRYPTION_KEY`, QRadar belirteci, `env.bak` ve `secrets.tar` içeriği, kullanıcı adları, müşteri verisi (log source adları, IP adresleri, URL yolları; kontrolün istediğinden fazlası). Gerekirse sayfa için yer tutucu kullan (`<site>`, `https://<host>`).

**Kaydet:** Teslim saati (UTC) ve teslim edilen dosyalar.
