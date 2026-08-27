# Test ve etkinleştirme

Sistemi gerçek bir raid'e güvenerek bırakmadan önce yapılacaklar. Dört
seviye var; her seviye bir öncekinin üstüne biniyor. **3. seviyeyi geçmeden
sisteme güvenme.**

| Seviye | Ne doğrular | Oyun gerekir mi | Süre |
|---|---|---|---|
| 0 · Kod | Mantık ve regresyon | Hayır | 10 sn |
| 1 · Boru hattı | Bildirim zinciri uçtan uca | Hayır | 5 dk |
| 2 · Bağlantı | Rust+ eşleşmesi ve sinyal akışı | Evet | 15 dk |
| 3 · Saha | Gerçek patlamayla tüm sistem | Evet | 20 dk |

---

## Seviye 0 — Kod testleri

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check src/ tests/ scripts/
```

**Beklenen:** 140 test geçer, lint temiz.

Neyi kanıtlar: puanlama eşikleri, toplayıcının gürültü bastırması, ETA'nın
bilmediğinde susması, bütçe tavanının araması engellemesi, ayar
doğrulaması, API sözleşmesi. Neyi kanıtlamaz: gerçek dünyayla temas eden
hiçbir şeyi.

---

## Seviye 1 — Boru hattı (oyun olmadan)

Rust+ eşleştirmesi olmadan arayüzü ve bildirim zincirini doğrular.

### 1.1 Demo panel

```bash
.venv/Scripts/python scripts/demo_panel.py
```

`http://127.0.0.1:8788/` aç.

**Beklenen:** Üstte "Kompound" saldırı kartı, işleyen geri sayım, güven
bandı, kalan yol merdiveni. Altı sekme çalışıyor. Sistem sekmesinde FCM
"sorunlu" ve Rust+ "kopuk" görünmeli — demo'da gerçek bağlantı yok, bu
**doğru** davranış.

### 1.2 Bildirim kanalları

Panel → Ayarlar → Discord webhook ve/veya ntfy konusu gir → Kaydet.

**Beklenen:** "N ayar güncellendi ve devreye girdi." Sistem sekmesinde
kanal listesi anında değişmeli — süreç yeniden başlamamalı.

Sonra "Bildirimleri dene".

**Beklenen:** Discord'a kırmızı çubuklu bir gömülü mesaj, telefonuna ntfy
push'u. Gelmiyorsa webhook adresini ve konu adını kontrol et.

### 1.3 Telefon *(opsiyonel, para harcar)*

Ayarlar → Twilio bilgileri + kişi listesi → Kaydet → "Telefonu dene".

**Beklenen:** Telefon çalar, mesajı okur. Sonuç panelde görünür:
`answered, 12 sn, $0.2875`.

> **Türkçe seslendirmeyi burada doğrula.** Twilio'nun desteklenen ses
> tablosunda `tr-TR` görünmüyor. Ses gelmiyorsa veya anlaşılmıyorsa
> Ayarlar'da `TWILIO_LANGUAGE=en-US`, `TWILIO_VOICE=Polly.Joanna` yap ve
> tekrar dene. Bunu raid sırasında öğrenmek istemezsin.

### 1.4 Bütçe tavanı

Ayarlar → aylık tavanı `0.01` yap → "Telefonu dene".

**Beklenen:** Arama **yapılmamalı**, "Arama bütçesi doldu" olayı düşmeli.
Sonra tavanı geri al.

---

## Seviye 2 — Bağlantı (oyunda)

### 2.1 Eşleştirme

```bash
sentinel pair      # bir kez
sentinel run       # açık bırak
```

Oyunda `ESC → Rust+ → Pair with Server`.

**Beklenen:** Panelde birkaç saniye içinde "Sunucu eşleştirildi" olayı,
Sistem sekmesinde Rust+ "bağlı".

**Olmuyorsa:** Sunucunun Rust+ desteği kapalı olabilir, ya da app portu
dışarıya kapalı. Ayarlar → Bağlantı → "Facepunch vekili üzerinden bağlan"
seçeneğini açıp dene.

### 2.2 Cihaz eşleştirme

Her Smart Alarm'a bak, `Pair` de.

**Beklenen:** Cihaz saniyeler içinde Cihazlar sekmesinde belirir. Adı
`Garaj S3` gibiyse bölge ve kademe otomatik dolar.

### 2.3 Bölge eşleşmesi

Panel → Kurulum sekmesi.

**Beklenen:** Yedi maddenin hepsi ✓. "Bölge adları üs tanımıyla eşleşiyor"
maddesi ✗ ise, uyuşmayan bölge adları listelenir — düzelt.

### 2.4 Sinyal akışı

Oyunda alarmı elle tetikle (sensöre yaklaş veya devreye kısa süreli güç ver).

**Beklenen:** Olaylar sekmesinde tetikleme görünür. Bu, FCM ve WebSocket
zincirinin gerçekten çalıştığının ilk kanıtı.

---

## Seviye 3 — Saha testi (gerçek patlama)

**Bu testi atlamak, sistemin çalıştığını varsaymak demektir.**

Üssüne kendi patlayıcınla vur. Feda edilebilir bir dış duvar seç.

### 3.1 Tek satchel

Sismik sensörün menzilinde bir satchel patlat.

**Beklenen:**
- Olaylar sekmesinde tetikleme
- `Garaj S1` ve `Garaj S2` yanmalı, `S3` yanmamalı
- Tehdit **ORTA**, telefon çalmamalı

Kademe yanlışsa devrende hata var — Branch'lerin ikisi de 1'e ayarlı mı?

### 3.2 Tek C4

**Beklenen:**
- Üç alarm da yanmalı (`S1`, `S2`, `S3`)
- Tehdit anında **YÜKSEK**
- Discord + ntfy + telefon zinciri tetiklenmeli
- ETA görünmeli

Bu, sistemin tek başına en önemli testi: bir C4 ikinci kanıt beklemeden
telefonu çaldırmalı.

### 3.3 Ardışık patlamalar

Üç C4'ü 20–30 saniye arayla patlat.

**Beklenen:**
- **Tek** "saldırı başladı" bildirimi, sonra ilerleme özetleri — üç ayrı
  kritik bildirim **gelmemeli**
- ETA'nın güveni "düşük"ten "orta"ya çıkmalı
- Telefon **bir kez** çalmalı (bölge bekleme süresi)

### 3.4 Yanlış alarm

Sadece HBHF sensörünün önünden geç (patlama yok).

**Beklenen:** Olaylar sekmesinde kaydedilir ama **hiçbir bildirim
gitmemeli**. Bu, sahte alarm filtresinin çalıştığının kanıtı.

### 3.5 Sessizlik

Tetiklemeyi kes, 5 dakika bekle.

**Beklenen:** "Saldırı durdu" özeti gelir, panelde aktif saldırı kalmaz.

---

## Doğrulanmamış varsayımlar

Bunlar kodda varsayım olarak duruyor ve ancak sahada anlaşılır. Seviye 3'te
gözle takip et:

| Varsayım | Yanlışsa ne olur | Nasıl anlarsın |
|---|---|---|
| Entity durum aboneliği alarmın ~3 dk bildirim cooldown'ına tabi değil | Ardışık patlamalar sayılamaz, ETA hız tahmini bozulur | 3.3'te tetikleme sayısı gerçekten 3 mü |
| Termometre devresi üç alarmı ayrı tetikliyor | Patlayıcı tipi ayırt edilemez | 3.1 ve 3.2'de hangi alarmların yandığı |
| Twilio Türkçe seslendiriyor | Arama sessiz veya anlaşılmaz | 1.3'te kulakla |
| 25 sn bölge geçiş süresi | ETA sistematik olarak kayar | 3.3'te tahmin ile gerçek arasındaki fark |

Sapma görürsen not al — hepsi tek bir yerde ayarlanabilir durumda.

---

## Sürekli izleme

Sistem çalışmaya başladıktan sonra tek bakılacak yer:

- **Panel → Sistem** — FCM sessizliği ve bağlantı durumu
- **`/health`** — dış izleme servisine bağla, sağlıksızsa 503 döner

Haftalık alışkanlık: wipe sonrası Kurulum sekmesine bak. Eşleştirme
geçersizleşmiş olur ve sensörler gitmiştir.
