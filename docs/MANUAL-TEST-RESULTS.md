# QRadar-Dash manuel test sonuç sayfası

Bu sayfa `docs/MANUAL-TEST.md` kılavuzunun **0.1.7** bölümünün sonuç sayfasıdır. Basılı alınabilir ya da bir kopyası doldurulabilir. Sonuç hücreleri boş bırakılmıştır: `Geçti`, `Kaldı` ya da `Koşulmadı` yaz; `Kaldı` ve `Koşulmadı` için Not yaz. Parola, belirteç, `.env` değeri ya da müşteri verisi **yazma**.

| Alan | Değer |
|---|---|
| Site | |
| Tarih (UTC) | |
| Operatör | |
| Sürüm önce | |
| Sürüm sonra | |
| Başlangıç (UTC) | |
| Bitiş (UTC) | |
| Yükseltme yolu (çevrimiçi / çevrimdışı paket) | |
| Migrasyon başı sonra (`0044`) | |
| Geri alma yapıldı mı (hayır / evet, saati) | |

**Çekirdek (üretim yolu)**, 38 kontrol: MT-01, MT-02, MT-03, MT-04, MT-05, MT-06, MT-07, MT-08, MT-09, MT-10, MT-11, MT-12, MT-13, MT-14, MT-15, MT-20, MT-21, MT-22, MT-24, MT-26, MT-27, MT-28, MT-29, MT-30, MT-38, MT-39, MT-44, MT-50, MT-51, MT-52, MT-55, MT-59, MT-95, MT-119, MT-120, MT-121, MT-124, MT-125. Üretimde yalnızca `Çekirdek` sütunu "evet" olan satırlar doldurulur; diğerleri laboratuvar koşusu içindir.

Sürüm önce: `0.1.6`, migrasyon başı `0032`. Sürüm sonra: `0.1.7`.

## 1. Ön koşullar

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-01 | evet | Yükseltmeden önceki sürüm ve sağlık | | |
| MT-02 | evet | `doctor` yükseltmeden önce yeşil | | |
| MT-03 | evet | Doğrulanmış yedek — (yazar) | | |
| MT-04 | evet | Günlüklerin ve destek paketinin yeri — (yazar) | | |
| MT-05 | evet | Pencere ve haberdar edilecek kişiler | | |

## 2. Yükseltme 0.1.6 → 0.1.7

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-06 | evet | Yükseltme, çevrimiçi yol — (yazar) | | |
| MT-07 | evet | Yükseltme, çevrimdışı paket yolu — (yazar) | | |
| MT-08 | evet | Yükseltmeden sonra sürüm ve durum | | |
| MT-09 | evet | `doctor` yükseltmeden hemen sonra | | |
| MT-10 | evet | Kesinti süresi ve arayüzün dönüşü | | |
| MT-11 | evet | İlk kadanstan sonra toplamanın dönmesi | | |

## 3. Oturum açma

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-12 | evet | Mevcut kullanıcının oturum açması | | |
| MT-13 | evet | Hesap menüsü ve API sürümü | | |
| MT-14 | evet | Hesap sayfası | | |
| MT-15 | evet | Oturumu kapatma ve yeniden açma | | |
| MT-16 |  | Yeni kullanıcı oluşturma — (yazar) yalnızca lab | | |
| MT-17 |  | Zorunlu parola değişikliği — (yazar) yalnızca lab | | |
| MT-18 |  | Görüntüleyici rolünün sınırı — (yazar) yalnızca lab | | |
| MT-19 |  | Test hesabını devre dışı bırakma — (yazar) yalnızca lab | | |

## 4. Dil

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-20 | evet | Dil anahtarı ve hatırlanması — (yazar) | | |
| MT-21 | evet | Sayı, oran ve zaman biçimi | | |
| MT-22 | evet | Üst çubuk ve gezinti Türkçe | | |
| MT-23 |  | Dil: Giriş sayfası `/login` | | |
| MT-24 | evet | Dil: Pano `/` | | |
| MT-25 |  | Dil: Davranış genel bakışı `/behavior` | | |
| MT-26 | evet | Dil: Anomali listesi `/anomalies` | | |
| MT-27 | evet | Dil: Anomali ayrıntısı, Phase A (log source) `/anomalies/<id>` | | |
| MT-28 | evet | Dil: Anomali ayrıntısı, WAF endpoint `/anomalies/<id>` | | |
| MT-29 | evet | Dil: Anomali ayrıntısı, IPS `/anomalies/<id>` | | |
| MT-30 | evet | Dil: Anomali ayrıntısı, özellik izleme değeri `/anomalies/<id>` | | |
| MT-31 |  | Dil: Kaynak davranışı `/behavior/sources` ve `/behavior/sources/<id>` | | |
| MT-32 |  | Dil: WAF davranışı `/behavior/waf` ve `/behavior/waf/sources/<id>` | | |
| MT-33 |  | Dil: Özellik izlemeleri `/behavior/watches`, `/behavior/watches/new`, `/behavior/watches/<id>` | | |
| MT-34 |  | Dil: Filo `/fleet` | | |
| MT-35 |  | Dil: Filo kaynakları `/fleet/sources` | | |
| MT-36 |  | Dil: Catch-all gönderenleri `/fleet/catchall` | | |
| MT-37 |  | Dil: Offense'lar `/offenses` ve `/offenses/<id>` | | |
| MT-38 | evet | Dil: Uyarılar `/alerts` ve `/alerts/<id>` | | |
| MT-39 | evet | Dil: Kural sağlığı `/rules` ve `/rules/<id>` | | |
| MT-40 |  | Dil: Kural istatistikleri `/rules/statistics` | | |
| MT-41 |  | Dil: Özel kurallar `/custom-rules`, `/custom-rules/new`, `/custom-rules/<id>` | | |
| MT-42 |  | Dil: Algılama coverage'ı `/coverage` | | |
| MT-43 |  | Dil: Log source'lar `/log-sources` ve `/log-sources/<id>` | | |
| MT-44 | evet | Dil: Aramalar `/searches`, `/searches/new`, `/searches/<id>` | | |
| MT-45 |  | Dil: Yapılandırma değişiklikleri `/config-changes` | | |
| MT-46 |  | Dil: Yönetim `/admin` | | |
| MT-47 |  | Dil: Arama sıklığı `/admin/search-cadence` | | |
| MT-48 |  | Dil: Sınıflama kuralları `/admin/classification-rules` | | |
| MT-49 |  | Dil: Kesinti uyarıları `/admin/outage-policy` | | |
| MT-50 | evet | Dil: WAF kaynakları `/admin/waf-sources` ve `/admin/waf-sources/<id>` | | |
| MT-51 | evet | Dil: IPS kaynakları `/admin/ips-sources` ve `/admin/ips-sources/<id>` | | |
| MT-52 | evet | Dil: Geçmiş veri okuma `/admin/history-backfill` | | |
| MT-53 |  | Dil: Kullanıcılar `/admin/users` | | |
| MT-54 |  | Dil: Dizin `/admin/directory` | | |
| MT-55 | evet | Dil: Kılavuz `/guide` ve `/guide/<bölüm>` | | |
| MT-56 |  | Dil: Hesap `/account` ve `/account/password` | | |
| MT-57 |  | Hatalı oturum açma cümlesi Türkçe — (yazar) | | |
| MT-58 |  | 0.1.6'dan kalan saklı cümleler Türkçede de İngilizcedir | | |
| MT-59 | evet | 0.1.7'de İngilizce kalması beklenenler | | |

## 5. Pano (Dashboard)

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-60 |  | Pano bileşeni: “Toplam EPS” | | |
| MT-61 |  | Pano bileşeni: “Log source'lar” | | |
| MT-62 |  | Pano bileşeni: “Açık offense'lar” | | |
| MT-63 |  | Pano bileşeni: “Etkin anomaliler” | | |
| MT-64 |  | Pano bileşeni: “Filo EPS” | | |
| MT-65 |  | Pano bileşeni: “Kaynak sınıfına göre EPS” | | |
| MT-66 |  | Pano bileşeni: “EPS'e göre en yoğun log source'lar” | | |
| MT-67 |  | Pano bileşeni: “Önem derecesine göre anomaliler” | | |
| MT-68 |  | Pano bileşeni: “Log source sağlığı” | | |
| MT-69 |  | Pano bileşeni: “Son anomaliler” | | |
| MT-70 |  | Pano bileşeni: “Son offense'lar” | | |
| MT-71 |  | Pano bileşeni: “Zamanlanmış aramalar” | | |
| MT-72 |  | Pano bileşeni: “Algılama sağlığı” | | |
| MT-73 |  | Pano bileşeni: “Kanıt toplama” | | |
| MT-74 |  | Pano bileşeni: “İncelenecek catch-all gönderenleri” | | |
| MT-75 |  | Pano bileşeni: “Kaynak sınıfları” | | |
| MT-76 |  | Pano bileşeni: “Kesinti politikası” | | |
| MT-77 |  | Pano bileşeni: “WAF kaynakları” | | |
| MT-78 |  | Pano bileşeni: “Son WAF anomalileri” | | |
| MT-79 |  | Pano bileşeni: “Son IPS anomalileri” | | |

## 6. Anomaliler

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-80 |  | Anomali listesi ve filtreler | | |
| MT-81 |  | Phase A anomali ayrıntısı ve dürüstlük durumları | | |
| MT-82 |  | Yaşam döngüsü sekmesi ve nedenler | | |
| MT-83 |  | WAF anomali ayrıntısı | | |
| MT-84 |  | IPS anomali ayrıntısı | | |
| MT-85 |  | Özellik izleme anomali ayrıntısı | | |
| MT-86 |  | Dürüstlük durumları görünür ve sıfıra çevrilmez | | |

## 7. Alarmlar (Uyarılar)

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-87 |  | Uyarı listesi | | |
| MT-88 |  | Uyarı ayrıntısı: Açıklama ve çözüm nedeni | | |
| MT-89 |  | Uyarıyı alındı yapma ve çözme — (yazar) yalnızca lab | | |

## 8. Özel kurallar

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-90 |  | Özel kural listesi ve durumları | | |
| MT-91 |  | Özel kural ayrıntısı | | |
| MT-92 |  | Özel kural taslağı önizlemesi (kaydetmeden) | | |
| MT-93 |  | Özel kural oluşturma ve silme — (yazar) | | |

## 9. Aramalar

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-94 |  | Arama listesi | | |
| MT-95 | evet | Arama çalıştırma geçmişi ve hata satırı | | |
| MT-96 |  | Zamanlama tabanı (cron) ve önayarlar | | |
| MT-97 |  | Aramayı şimdi çalıştırma — (yazar) yalnızca lab QRadar araması | | |
| MT-98 |  | Yeni arama saklama — (yazar) yalnızca lab | | |

## 10. Geçmiş okuma

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-99 |  | Geçmiş okuma sayfası: karar bloğu ve tablo | | |
| MT-100 |  | Geçmiş okuma: ilk açılış sorusu | | |
| MT-101 |  | Geçmişi şimdi oku / Okumayı durdur — (yazar) yalnızca lab QRadar araması | | |

## 11. Kural sağlığı, istatistikler, coverage ve filo

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-102 |  | Kural sağlığı listesi | | |
| MT-103 |  | Kural ayrıntısı ve sağlık geçmişi | | |
| MT-104 |  | Kural istatistikleri | | |
| MT-105 |  | Coverage | | |
| MT-106 |  | Filo, filo kaynakları ve catch-all gönderenleri | | |
| MT-107 |  | Log source'lar ve ayrıntı | | |
| MT-108 |  | Yapılandırma değişiklikleri | | |
| MT-109 |  | Davranış sayfaları: genel bakış, kaynaklar, WAF, özellik izlemeleri, offense'lar | | |

## 12. Yönetim

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-110 |  | WAF kaynakları: liste ve profil | | |
| MT-111 |  | IPS kaynakları: liste ve profil | | |
| MT-112 |  | Kaynak doğrulamasını yeniden çalıştırma — (yazar) yalnızca lab QRadar araması | | |
| MT-113 |  | Kullanıcılar ve dizin (yalnızca görüntüleme) | | |
| MT-114 |  | Arama sıklığı (yalnızca görüntüleme) | | |
| MT-115 |  | Arama sıklığını değiştirme — (yazar) yalnızca lab | | |
| MT-116 |  | Kesinti politikası ve sınıflama kuralları (yalnızca görüntüleme) | | |
| MT-117 |  | Yönetim ana sayfası | | |

## 13. Kılavuz

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-118 |  | Kılavuz iki dilde açılır | | |

## 14. QRadar'a karşı salt okunur

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-119 | evet | `doctor` QRadar'a tek çağrı yaptığını gösterir | | |
| MT-120 | evet | Konsol tarafı: test penceresinde yazma işlemi olmadığının doğrulanması — konsol yöneticisi adımı | | |

## 15. Geri alma ölçütleri ve adımları

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-121 | evet | Geri alma ölçütlerinin değerlendirilmesi | | |
| MT-122 |  | Geri alma (yalnızca gerekirse) — (yazar) | | |
| MT-123 |  | Geri alma provası — (yazar) yalnızca lab | | |

## 16. Sonuçların teslimi

| Kimlik | Çekirdek | Kontrol (kısa) | Sonuç (Geçti / Kaldı / Koşulmadı) | Not |
|---|---|---|---|---|
| MT-124 | evet | Dil, tur C: İngilizceye dönüş — (yazar) | | |
| MT-125 | evet | Sonuç sayfasını doldur ve teslim et | | |

## Özet

| Sayım | Değer |
|---|---|
| Geçti | |
| Kaldı | |
| Koşulmadı | |
| Destek paketi eklendi mi (evet / hayır) | |
| Teslim saati (UTC) | |
