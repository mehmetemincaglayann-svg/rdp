# PyRemote

Python ile yazılmış, TeamViewer benzeri uzak masaüstü kontrol programı.

- Ekran görüntüleme, fare ve klavye kontrolü
- ID + parola ile internet üzerinden bağlantı (aktarma sunucusu aracılığıyla)
- Dosya gönderme / alma, pano (kopyala-yapıştır) paylaşımı
- Çoklu monitör, görüntü kalitesi seçimi
- Gözetimsiz erişim (kalıcı parola)
- Uçtan uca şifreleme (SPAKE2 + ChaCha20-Poly1305)

## Parçalar

| Parça | Nerede çalışır | Komut |
|---|---|---|
| Aktarma sunucusu | Herkesin erişebildiği bir sunucu (VPS) | `python -m pyremote.relay` |
| Ana makine | Kontrol edilecek Windows bilgisayar | `python -m pyremote.host` |
| İzleyici | Kontrol eden bilgisayar | `python -m pyremote.viewer` |

## Kurulum

Python 3.10 veya üstü gerekir.

```bash
pip install -r requirements.txt
```

## Aynı bilgisayarda hızlı deneme

Üç ayrı terminalde:

```bash
python -m pyremote.relay --host 127.0.0.1
```

```bash
python -m pyremote.host
```

```bash
python -m pyremote.viewer
```

Ana makine penceresindeki ID ve parolayı izleyiciye girin.

## İnternet üzerinden kullanım

Farklı ağlardaki iki bilgisayarın birbirini bulabilmesi için aktarma sunucusunun
internette herkese açık bir adreste çalışması gerekir.

### Seçenek 1: Bulut hizmetine kurma (Docker)

Depodaki `Dockerfile` yalnızca aktarma sunucusunu çalıştırır ve bağlantı noktasını
`PORT` ortam değişkeninden alır; Docker imajı çalıştırabilen ve WebSocket destekleyen
her hizmette (Render, Fly.io, Koyeb, Railway...) kullanılabilir. Render için
`render.yaml` hazırdır: Render'da "New → Blueprint" ile bu depoyu seçmek yeterlidir.
Hizmet size `https://ad.onrender.com` gibi bir adres verir; programda bunu
`wss://ad.onrender.com` olarak girin.

Ücretsiz planlar genellikle kullanılmadığında hizmeti uyutur; ilk bağlantı bu yüzden
gecikebilir. ID kayıtları kalıcı diske yazılmadığı için hizmet yeniden başlayınca
sıfırlanır; bu, kullanımı etkilemez.

### Seçenek 2: Kendi sunucunuz (VPS)

```bash
python -m pyremote.relay --host 0.0.0.0 --port 8765
```

8765 numaralı bağlantı noktasını açın ve adres olarak `ws://SUNUCU_IP:8765` kullanın.
TLS için `--cert` ve `--key` verip `wss://` kullanabilir ya da sunucuyu Caddy/nginx
arkasına alabilirsiniz.

### Adresi programa girme

- Ana makine: penceredeki **Sunucu adresi…** düğmesi (ya da `--relay ADRES`).
- İzleyici: bağlantı ekranındaki **Sunucu** alanı.

Adres kaydedilir; iki tarafta da aynı adres girilmelidir. Oturum içeriği uçtan uca
şifrelidir, sunucuyu işleten taraf ekranı ya da parolayı göremez.

## Gözetimsiz erişim

Ana makine penceresinde **Kalıcı parola ayarla…** düğmesini kullanın ya da:

```bash
python -m pyremote.host --set-password
```

Ana makinenin oturum açılınca kendiliğinden başlaması için Görev Zamanlayıcı'da
"oturum açıldığında" tetiklenen bir görev oluşturun; komut olarak
`pythonw -m pyremote.host` verin. Kalıcı parolayı kaldırmak için pencere
düğmesini ya da `--clear-password` seçeneğini kullanın.

## Tek dosyalık .exe üretme

PyInstaller yalnızca çalıştığı işletim sistemi için derler; bu yüzden derleme
Windows'ta yapılmalıdır. Windows'ta `build.bat` dosyasına çift tıklayın. Bittiğinde
`dist` klasöründe üç dosya oluşur:

- `PyRemoteHost.exe` – kontrol edilecek bilgisayarda çalışır
- `PyRemoteViewer.exe` – kontrol eden bilgisayarda çalışır
- `PyRemoteRelay.exe` – aktarma sunucusu (Windows sunucuda barındıracaksanız)

Proje GitHub'daysa `.github/workflows/build.yml` aynı dosyaları bulutta üretir:
Actions sekmesinden "Windows exe" iş akışını çalıştırıp çıktıyı indirin.

İmzasız `.exe` dosyaları için Windows SmartScreen ve bazı antivirüsler uyarı verebilir.

## Güvenlik modeli

- Parola ağ üzerinden hiç gönderilmez. İki taraf SPAKE2 ile parolayı bildiklerini
  kanıtlar ve bir oturum anahtarı üretir; aktarma sunucusu ne parolayı ne de
  içeriği görebilir, parolayı çevrimdışı deneme-yanılma ile de kıramaz.
- Art arda 5 hatalı denemeden sonra ana makine girişleri 5 dakika (her yeni
  hatada katlanarak, en çok 1 saat) kilitler.
- Kalıcı parola diskte düz metin olarak saklanmaz (scrypt ile türetilmiş anahtar saklanır).
- Ana makine penceresi, bağlantı varken kırmızı bir uyarı ve bağlanan IP adresini gösterir.
- Gönderilen dosyalar ana makinede yalnızca `İndirilenler` klasörüne yazılır ve var
  olan dosyanın üzerine yazılmaz. Bağlanan kişi ise ana makinedeki kullanıcının
  okuyabildiği her dosyayı indirebilir; parolayı buna göre koruyun.

## Bilinen sınırlar

- Fare/klavye kontrolü yalnızca Windows ana makinede çalışır (diğer sistemlerde yalnızca izleme).
- UAC onay pencereleri, kilit ekranı ve yönetici olarak çalışan programlar, ana makine
  normal kullanıcı olarak çalışırken kontrol edilemez (Windows kısıtı). Ctrl+Alt+Del gönderilemez.
- Trafiğin tamamı aktarma sunucusundan geçer (doğrudan P2P bağlantı yok).
- Görüntü JPEG olarak ve yalnızca değişen bölge gönderilerek aktarılır; video ve oyun için uygun değildir.
- Ses aktarımı yoktur. Pano yalnızca metin taşır.

## Test

```bash
python tests/test_e2e.py
```

## Dizin yapısı

```
pyremote/
  relay.py          aktarma sunucusu
  crypto.py         SPAKE2 el sıkışması, şifreli kanal, deneme kilidi
  protocol.py       ileti türleri
  config.py         ayar dosyası (ID, sunucu adresi, kalıcı parola anahtarı)
  host/service.py   oturum yönetimi: ekran, girdi, pano, dosya
  host/backend.py   ekran yakalama (mss) ve Windows girdisi
  host/win_input.py SendInput ile fare/klavye
  host/keymap.py    tuş çevirisi
  host/gui.py       ana makine penceresi
  viewer/client.py  izleyicinin ağ tarafı
  viewer/gui.py     izleyici penceresi
```
