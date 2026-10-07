# SH-Kanta-validator

Streamlit-käyttöliittymä Kannan SHARK-validointipalvelulle. Sovellus lukee
siirtokehyksen ja CDA-asiakirjan, tarkistaa syötteet paikallisesti ja lähettää
ne käyttäjän käynnistämällä pyynnöllä validointipalveluun.

## Asennus ja käynnistys

Käytä Python 3.10:tä tai uudempaa. Luo virtuaaliympäristö projektin hakemistossa
ja asenna määritellyt riippuvuudet ennen käynnistystä:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python SHKanta-www-validator.py
```

Suora Python-käynnistys avaa Streamlit-sovelluksen samassa Python-ympäristössä.
macOS- ja Linux-ympäristöissä voit käynnistää tiedoston myös komennolla
`./SHKanta-www-validator.py`. Vaihtoehtoinen käynnistystapa on edelleen
`streamlit run SHKanta-www-validator.py`. Mahdolliset Streamlitin
komentoriviargumentit välitetään myös suorasta käynnistyksestä, esimerkiksi
`python SHKanta-www-validator.py --server.port 8502`.

Sovellus käyttää koodissa määriteltyä, julkaistun käyttöohjeen mukaista
HTTP-palveluosoitetta. Käynnistys ei asenna paketteja automaattisesti.

## Syötteet ja henkilötunnusten tarkistus

Syötä siirtokehys ja CDA-asiakirja XML-tiedostoina tai tekstinä. Asiakirjan on
sisällettävä HL7:n `urn:hl7-org:v3`-nimialueen `ClinicalDocument`-elementti.
Siirtokehyksen palvelupyyntökoodin on oltava yksiselitteinen. Kehyksen
`id/@root`- ja `setId/@root`-tunnisteiden on täsmättävä asiakirjan tunnisteisiin
ennen lähetystä. Palvelupyyntö erotetaan muista `reasonCode`-elementeistä
sen koodistotunnisteen perusteella.

Käytä vain testiaineistoa. Sovellus estää lähetyksen, jos se löytää kelvollisen
suomalaisen henkilötunnuksen, jonka yksilönumero ei ala numerolla 9.
Yksilönumero tarkoittaa kolmea numeroa vuosisatamerkin ja tarkistusmerkin välissä.
Tunnuksen päivämäärän ja tarkistusmerkin on myös oltava kelvollisia.

Paikallinen tarkistus käsittelee XML:n tekstit ja attribuutit sekä tuetut
tekstimuotoiset upotetut sisällöt, kuten XML- ja JSON-tekstin ja Base64:ksi
koodatun tekstin. Tarkistus ei pura binäärisiä liitteitä. Jos syötteessä on
nimenomaisesti ilmoitettu binäärinen sisältö, jota tarkistus ei tue, lähetys
pysäytetään. Henkilötunnusten tarkistus ei korvaa aineiston anonymisointia:
myös muut henkilötiedot on poistettava testiaineistosta.

## Vastaukset

Onnistunut validointi on tyhjä HTTP 200 -vastaus. Virhevastaukset säilyvät
käyttöliittymässä ja ovat ladattavissa myös silloin, kun JSON-vastauksesta
puuttuu tekstimuotoinen `description`-kenttä. HTML-vastaukset näytetään tekstinä.
HTTP 200 -vastauksen odottamaton sisältö näytetään tarkistettavana vastauksena,
eikä sitä tulkita onnistuneeksi validoinniksi.

Lähetettäviin XML-kenttiin lisätään tarvittaessa yksi XML-alkumäärittely ja
CRLF/CR-rivinvaihdot muunnetaan LF-muotoon. Tämä varmistaa yhteensopivuuden
validointipalvelun kanssa; alkuperäisiä tiedostoja ei muokata.
`Request Rejected` -HTML-vastauksen tukitunniste näytetään virheilmoituksessa.

## Testit

Regressiotestit käyttävät Pythonin standardikirjaston `unittest`-moduulia.
Ne eivät lähetä sanomia validointipalveluun:

```sh
python -m unittest discover -s tests
```
