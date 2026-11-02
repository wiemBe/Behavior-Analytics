"""Static ISO 3166-1 table for a geography property value (GEO-3).

249 assigned alpha-2 codes, the alpha-3 code and the English short name.
Aliases are common English forms of the same country. The table is static:
collecting a country does not fetch it, and a property value is matched
here and then dropped. No event text is stored in this module.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def normalise_country_token(text: str) -> str:
    """Letters and digits only, case-folded, accents removed.

    Spaces, apostrophes, hyphens and dots disappear, so ``United States``,
    ``UnitedStates`` and ``U.S.`` compare as the same kind of token.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    folded = without_marks.casefold()
    return "".join(
        character
        for character in folded
        if "a" <= character <= "z" or "0" <= character <= "9"
    )


@dataclass(frozen=True)
class Country:
    """One assigned ISO 3166-1 country and the English forms that name it."""

    alpha2: str
    alpha3: str
    name: str
    aliases: tuple[str, ...] = ()


COUNTRIES: tuple[Country, ...] = (
    Country("AF", "AFG", "Afghanistan"),
    Country("AX", "ALA", "Åland Islands"),
    Country("AL", "ALB", "Albania"),
    Country("DZ", "DZA", "Algeria"),
    Country("AS", "ASM", "American Samoa"),
    Country("AD", "AND", "Andorra"),
    Country("AO", "AGO", "Angola"),
    Country("AI", "AIA", "Anguilla"),
    Country("AQ", "ATA", "Antarctica"),
    Country("AG", "ATG", "Antigua and Barbuda"),
    Country("AR", "ARG", "Argentina"),
    Country("AM", "ARM", "Armenia"),
    Country("AW", "ABW", "Aruba"),
    Country("AU", "AUS", "Australia"),
    Country("AT", "AUT", "Austria"),
    Country("AZ", "AZE", "Azerbaijan"),
    Country("BS", "BHS", "Bahamas", ("The Bahamas",)),
    Country("BH", "BHR", "Bahrain"),
    Country("BD", "BGD", "Bangladesh"),
    Country("BB", "BRB", "Barbados"),
    Country("BY", "BLR", "Belarus"),
    Country("BE", "BEL", "Belgium"),
    Country("BZ", "BLZ", "Belize"),
    Country("BJ", "BEN", "Benin"),
    Country("BM", "BMU", "Bermuda"),
    Country("BT", "BTN", "Bhutan"),
    Country("BO", "BOL", "Bolivia, Plurinational State of", ("Bolivia",)),
    Country("BQ", "BES", "Bonaire, Sint Eustatius and Saba"),
    Country("BA", "BIH", "Bosnia and Herzegovina"),
    Country("BW", "BWA", "Botswana"),
    Country("BV", "BVT", "Bouvet Island"),
    Country("BR", "BRA", "Brazil"),
    Country("IO", "IOT", "British Indian Ocean Territory"),
    Country("BN", "BRN", "Brunei Darussalam", ("Brunei",)),
    Country("BG", "BGR", "Bulgaria"),
    Country("BF", "BFA", "Burkina Faso"),
    Country("BI", "BDI", "Burundi"),
    Country("CV", "CPV", "Cabo Verde", ("Cape Verde",)),
    Country("KH", "KHM", "Cambodia"),
    Country("CM", "CMR", "Cameroon"),
    Country("CA", "CAN", "Canada"),
    Country("KY", "CYM", "Cayman Islands"),
    Country("CF", "CAF", "Central African Republic"),
    Country("TD", "TCD", "Chad"),
    Country("CL", "CHL", "Chile"),
    Country("CN", "CHN", "China", ("People's Republic of China", "PRC")),
    Country("CX", "CXR", "Christmas Island"),
    Country("CC", "CCK", "Cocos (Keeling) Islands"),
    Country("CO", "COL", "Colombia"),
    Country("KM", "COM", "Comoros"),
    Country("CG", "COG", "Congo", ("Republic of the Congo", "Congo Republic")),
    Country(
        "CD",
        "COD",
        "Congo, Democratic Republic of the",
        ("Democratic Republic of the Congo", "DRC", "DR Congo"),
    ),
    Country("CK", "COK", "Cook Islands"),
    Country("CR", "CRI", "Costa Rica"),
    Country("CI", "CIV", "Côte d'Ivoire", ("Ivory Coast",)),
    Country("HR", "HRV", "Croatia"),
    Country("CU", "CUB", "Cuba"),
    Country("CW", "CUW", "Curaçao"),
    Country("CY", "CYP", "Cyprus"),
    Country("CZ", "CZE", "Czechia", ("Czech Republic",)),
    Country("DK", "DNK", "Denmark"),
    Country("DJ", "DJI", "Djibouti"),
    Country("DM", "DMA", "Dominica"),
    Country("DO", "DOM", "Dominican Republic"),
    Country("EC", "ECU", "Ecuador"),
    Country("EG", "EGY", "Egypt"),
    Country("SV", "SLV", "El Salvador"),
    Country("GQ", "GNQ", "Equatorial Guinea"),
    Country("ER", "ERI", "Eritrea"),
    Country("EE", "EST", "Estonia"),
    Country("SZ", "SWZ", "Eswatini", ("Swaziland",)),
    Country("ET", "ETH", "Ethiopia"),
    Country("FK", "FLK", "Falkland Islands (Malvinas)", ("Falkland Islands",)),
    Country("FO", "FRO", "Faroe Islands"),
    Country("FJ", "FJI", "Fiji"),
    Country("FI", "FIN", "Finland"),
    Country("FR", "FRA", "France"),
    Country("GF", "GUF", "French Guiana"),
    Country("PF", "PYF", "French Polynesia"),
    Country("TF", "ATF", "French Southern Territories", ("French Southern and Antarctic Lands",)),
    Country("GA", "GAB", "Gabon"),
    Country("GM", "GMB", "Gambia", ("The Gambia",)),
    Country("GE", "GEO", "Georgia"),
    Country("DE", "DEU", "Germany"),
    Country("GH", "GHA", "Ghana"),
    Country("GI", "GIB", "Gibraltar"),
    Country("GR", "GRC", "Greece"),
    Country("GL", "GRL", "Greenland"),
    Country("GD", "GRD", "Grenada"),
    Country("GP", "GLP", "Guadeloupe"),
    Country("GU", "GUM", "Guam"),
    Country("GT", "GTM", "Guatemala"),
    Country("GG", "GGY", "Guernsey"),
    Country("GN", "GIN", "Guinea"),
    Country("GW", "GNB", "Guinea-Bissau"),
    Country("GY", "GUY", "Guyana"),
    Country("HT", "HTI", "Haiti"),
    Country("HM", "HMD", "Heard Island and McDonald Islands"),
    Country("VA", "VAT", "Holy See", ("Vatican", "Vatican City")),
    Country("HN", "HND", "Honduras"),
    Country("HK", "HKG", "Hong Kong"),
    Country("HU", "HUN", "Hungary"),
    Country("IS", "ISL", "Iceland"),
    Country("IN", "IND", "India"),
    Country("ID", "IDN", "Indonesia"),
    Country("IR", "IRN", "Iran, Islamic Republic of", ("Iran",)),
    Country("IQ", "IRQ", "Iraq"),
    Country("IE", "IRL", "Ireland"),
    Country("IM", "IMN", "Isle of Man"),
    Country("IL", "ISR", "Israel"),
    Country("IT", "ITA", "Italy"),
    Country("JM", "JAM", "Jamaica"),
    Country("JP", "JPN", "Japan"),
    Country("JE", "JEY", "Jersey"),
    Country("JO", "JOR", "Jordan"),
    Country("KZ", "KAZ", "Kazakhstan"),
    Country("KE", "KEN", "Kenya"),
    Country("KI", "KIR", "Kiribati"),
    Country("KP", "PRK", "Korea, Democratic People's Republic of", ("North Korea", "DPRK")),
    Country("KR", "KOR", "Korea, Republic of", ("South Korea", "Republic of Korea")),
    Country("KW", "KWT", "Kuwait"),
    Country("KG", "KGZ", "Kyrgyzstan"),
    Country("LA", "LAO", "Lao People's Democratic Republic", ("Laos",)),
    Country("LV", "LVA", "Latvia"),
    Country("LB", "LBN", "Lebanon"),
    Country("LS", "LSO", "Lesotho"),
    Country("LR", "LBR", "Liberia"),
    Country("LY", "LBY", "Libya"),
    Country("LI", "LIE", "Liechtenstein"),
    Country("LT", "LTU", "Lithuania"),
    Country("LU", "LUX", "Luxembourg"),
    Country("MO", "MAC", "Macao"),
    Country("MG", "MDG", "Madagascar"),
    Country("MW", "MWI", "Malawi"),
    Country("MY", "MYS", "Malaysia"),
    Country("MV", "MDV", "Maldives"),
    Country("ML", "MLI", "Mali"),
    Country("MT", "MLT", "Malta"),
    Country("MH", "MHL", "Marshall Islands"),
    Country("MQ", "MTQ", "Martinique"),
    Country("MR", "MRT", "Mauritania"),
    Country("MU", "MUS", "Mauritius"),
    Country("YT", "MYT", "Mayotte"),
    Country("MX", "MEX", "Mexico"),
    Country("FM", "FSM", "Micronesia, Federated States of", ("Micronesia",)),
    Country("MD", "MDA", "Moldova, Republic of", ("Moldova",)),
    Country("MC", "MCO", "Monaco"),
    Country("MN", "MNG", "Mongolia"),
    Country("ME", "MNE", "Montenegro"),
    Country("MS", "MSR", "Montserrat"),
    Country("MA", "MAR", "Morocco"),
    Country("MZ", "MOZ", "Mozambique"),
    Country("MM", "MMR", "Myanmar", ("Burma",)),
    Country("NA", "NAM", "Namibia"),
    Country("NR", "NRU", "Nauru"),
    Country("NP", "NPL", "Nepal"),
    Country("NL", "NLD", "Netherlands"),
    Country("NC", "NCL", "New Caledonia"),
    Country("NZ", "NZL", "New Zealand"),
    Country("NI", "NIC", "Nicaragua"),
    Country("NE", "NER", "Niger"),
    Country("NG", "NGA", "Nigeria"),
    Country("NU", "NIU", "Niue"),
    Country("NF", "NFK", "Norfolk Island"),
    Country("MK", "MKD", "North Macedonia", ("Macedonia",)),
    Country("MP", "MNP", "Northern Mariana Islands"),
    Country("NO", "NOR", "Norway"),
    Country("OM", "OMN", "Oman"),
    Country("PK", "PAK", "Pakistan"),
    Country("PW", "PLW", "Palau"),
    Country("PS", "PSE", "Palestine, State of", ("Palestine",)),
    Country("PA", "PAN", "Panama"),
    Country("PG", "PNG", "Papua New Guinea"),
    Country("PY", "PRY", "Paraguay"),
    Country("PE", "PER", "Peru"),
    Country("PH", "PHL", "Philippines"),
    Country("PN", "PCN", "Pitcairn"),
    Country("PL", "POL", "Poland"),
    Country("PT", "PRT", "Portugal"),
    Country("PR", "PRI", "Puerto Rico"),
    Country("QA", "QAT", "Qatar"),
    Country("RE", "REU", "Réunion"),
    Country("RO", "ROU", "Romania"),
    Country("RU", "RUS", "Russian Federation", ("Russia",)),
    Country("RW", "RWA", "Rwanda"),
    Country("BL", "BLM", "Saint Barthélemy"),
    Country("SH", "SHN", "Saint Helena, Ascension and Tristan da Cunha"),
    Country("KN", "KNA", "Saint Kitts and Nevis"),
    Country("LC", "LCA", "Saint Lucia"),
    Country("MF", "MAF", "Saint Martin (French part)"),
    Country("PM", "SPM", "Saint Pierre and Miquelon"),
    Country("VC", "VCT", "Saint Vincent and the Grenadines"),
    Country("WS", "WSM", "Samoa"),
    Country("SM", "SMR", "San Marino"),
    Country("ST", "STP", "Sao Tome and Principe"),
    Country("SA", "SAU", "Saudi Arabia"),
    Country("SN", "SEN", "Senegal"),
    Country("RS", "SRB", "Serbia"),
    Country("SC", "SYC", "Seychelles"),
    Country("SL", "SLE", "Sierra Leone"),
    Country("SG", "SGP", "Singapore"),
    Country("SX", "SXM", "Sint Maarten (Dutch part)"),
    Country("SK", "SVK", "Slovakia"),
    Country("SI", "SVN", "Slovenia"),
    Country("SB", "SLB", "Solomon Islands"),
    Country("SO", "SOM", "Somalia"),
    Country("ZA", "ZAF", "South Africa"),
    Country("GS", "SGS", "South Georgia and the South Sandwich Islands"),
    Country("SS", "SSD", "South Sudan"),
    Country("ES", "ESP", "Spain"),
    Country("LK", "LKA", "Sri Lanka"),
    Country("SD", "SDN", "Sudan"),
    Country("SR", "SUR", "Suriname"),
    Country("SJ", "SJM", "Svalbard and Jan Mayen"),
    Country("SE", "SWE", "Sweden"),
    Country("CH", "CHE", "Switzerland"),
    Country("SY", "SYR", "Syrian Arab Republic", ("Syria",)),
    Country("TW", "TWN", "Taiwan, Province of China", ("Taiwan",)),
    Country("TJ", "TJK", "Tajikistan"),
    Country("TZ", "TZA", "Tanzania, United Republic of", ("Tanzania",)),
    Country("TH", "THA", "Thailand"),
    Country("TL", "TLS", "Timor-Leste", ("East Timor",)),
    Country("TG", "TGO", "Togo"),
    Country("TK", "TKL", "Tokelau"),
    Country("TO", "TON", "Tonga"),
    Country("TT", "TTO", "Trinidad and Tobago"),
    Country("TN", "TUN", "Tunisia"),
    Country("TR", "TUR", "Türkiye", ("Turkey",)),
    Country("TM", "TKM", "Turkmenistan"),
    Country("TC", "TCA", "Turks and Caicos Islands"),
    Country("TV", "TUV", "Tuvalu"),
    Country("UG", "UGA", "Uganda"),
    Country("UA", "UKR", "Ukraine"),
    Country("AE", "ARE", "United Arab Emirates", ("UAE",)),
    Country(
        "GB",
        "GBR",
        "United Kingdom of Great Britain and Northern Ireland",
        ("United Kingdom", "UK", "Great Britain"),
    ),
    Country("US", "USA", "United States of America", ("United States",)),
    Country("UM", "UMI", "United States Minor Outlying Islands"),
    Country("UY", "URY", "Uruguay"),
    Country("UZ", "UZB", "Uzbekistan"),
    Country("VU", "VUT", "Vanuatu"),
    Country("VE", "VEN", "Venezuela, Bolivarian Republic of", ("Venezuela",)),
    Country("VN", "VNM", "Viet Nam", ("Vietnam",)),
    Country("VG", "VGB", "Virgin Islands (British)"),
    Country("VI", "VIR", "Virgin Islands (U.S.)"),
    Country("WF", "WLF", "Wallis and Futuna"),
    Country("EH", "ESH", "Western Sahara"),
    Country("YE", "YEM", "Yemen"),
    Country("ZM", "ZMB", "Zambia"),
    Country("ZW", "ZWE", "Zimbabwe"),
)


def _build_index() -> dict[str, str]:
    """Map every normalised form to its alpha-2. Two countries cannot share one."""
    index: dict[str, str] = {}

    def add(token: str, alpha2: str) -> None:
        key = normalise_country_token(token)
        if not key:
            raise RuntimeError("country table has an empty token")
        previous = index.get(key)
        if previous is not None and previous != alpha2:
            raise RuntimeError(f"country token {key!r} names {previous} and {alpha2}")
        index[key] = alpha2

    for country in COUNTRIES:
        add(country.alpha2, country.alpha2)
        add(country.alpha3, country.alpha2)
        add(country.name, country.alpha2)
        for alias in country.aliases:
            add(alias, country.alpha2)
    return index


_INDEX: dict[str, str] = _build_index()


def country_code_for(value: str) -> str | None:
    """The alpha-2 for a property value, or None when it names no country.

    The whole value is tried first, so a short name that contains a dot
    (``Virgin Islands (U.S.)``) is not split. Otherwise a dotted location
    uses its last segment (``Europe.Turkey``, ``NorthAmerica.UnitedStates``).
    """
    token = value.strip()
    if not token:
        return None
    direct = _INDEX.get(normalise_country_token(token))
    if direct is not None:
        return direct
    if "." not in token:
        return None
    last = token.rsplit(".", 1)[-1].strip()
    if not last or last == token:
        return None
    return _INDEX.get(normalise_country_token(last))
