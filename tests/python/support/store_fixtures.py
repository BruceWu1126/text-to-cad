"""Store fixtures for tests: build a tree and lay a view of it, or seed a
document's result straight into the store without a kernel."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


def build_view(
    compound: Any,
    *,
    package_dir: Path,
    root_name: str,
    force: bool = False,
    provenance: Mapping[str, Any] | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Build ``compound``'s tree into the store and lay a view of it at
    ``package_dir`` (the directory shape the older readers consume). Returns the
    build stats plus the tree hash under ``tree``."""
    from cadgen.store.build import build_tree_from_compound
    from cadgen.store.view import export_view

    tree_hash, _tree, stats = build_tree_from_compound(
        compound,
        root_name=root_name,
        force=force,
        progress=progress,
    )
    export_view(tree_hash, Path(package_dir))
    result = dict(stats)
    result["tree"] = tree_hash
    return result


# Proven small box inputs, and each one's part as cadgen derives it under the named
# producer: its display mesh (``glb``) and selector table (``selectors``), zlib-compressed --
# what ``cadgen.store.parts`` derives from ``payload`` under FIXTURE_PRODUCER
# (test_store_seed_inputs.py holds them to it). Keeping encoded inputs here lets catalog and
# security fixtures seed genuine geometry and display data without importing OCP in the process
# under test. A new PART_VERSION, or another producer, names another part: derive them again.
FIXTURE_PRODUCER = {'build123d': '0.11.1', 'ocp': '7.9.3.1', 'cadqueryOcp': '7.9.3.1.1'}
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': '01e2a28516540a13ff82f3e0479e481eb16c24c2f60cad2cb2e518d90eecee41', 'payload': 'eJylVttu00AQXTvXuumFFih3DE8Q8QBpxCN1lSLxgFSkRLxbaRsqRXGUEKTyxHfwE/wAUvsX/EbEFzBOdmYTeyZrB6uKVzuzc87MnB3XOx2eD/zWcbt1fPLe70TDqB/1rvzPzVf+i+5Lf24Nx93w7Nz7GHXDr5fRYOy/9lqT0bfzxtnYbzQ9R7HP9AhXWR2qgeB4LUQwkZION+tyECHyc7BBoCF/HcwJG4ebdTlIzxocsjpkr0PuUtsjBP/NIRVhdkfG/puGdEOys1MJMj+SWAKZ3Aj0SAhS20WE5AFrDrSBjnkR6JEQ5IjJAJY+2A8EeRECG8KnqH/ViwaHJzCF9fp00BldhoPepE/zuT0ZXYRdUN9bm/jSCNn8pte4yi2l1QC5u5A9kETIdntSB/IC5x4AUiCJkHUoC4EsfqbJKYV5nfaXcAgKO2x6lQ+/fv/8++f7Oz5q3ChHOfBXt3jOU5l5ltHTmW3wcV1HSfoDowsvdxWj+PBz3Hum3yLHIJXNEkceSQVuQeQIxiL8FgQjcXyKW0jWxlEpniOPBKUqiRzBWIZXUTCmOT5GjkvI/HkoQEVEBmMVfkuCkZAR0HSwNE9eOb7ecZ6gzyNcPETnoiKeFNyZK+4B+iTKzX3TeIFzF5JrDJ8k1H4DXmXJ6KkMAr+Hewe2bIJUNksceRrQpU2RIxhrKoPA7+AWkrVxjB+OY4UFglJtiRzBuK0yCJw47iPHJeSqkKO7IyKDcVdlEDgCmg6SwO/qHec2+uzh4hY6G4EbGlrgu2wqGyyf6ZEbh+T1BsY9MZW4eToVzMDc1SVkTwq+vwo5zp2X2AIyXQOaoVTEbb1D1VRbuPDR2RTR0NBFrLGpbEpsYx3xYgNjzMBaRNIDP2prwnn3YBVyXB9rEekO4LQ3RcTWOSRAkhHO3IUiGhq6iHh9TEAc0EYUqGiFDVsIaIqmA6LWTUD8Bhh906XBfi8ENLXQAZFyvYAB76PHDnpS03EOmcGO8eou/jeDQqrT9+8fa241eQ==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTiRvTY25lJS2kqpEoiSKJcoh8VeqFWwkTF5KELin/Xan9VZew2LeYRGKpJ35xvPfDM7swM0W5fflxBCn/IIfV1F6MdFrfqIabfLIlx6xLcs7HqBj0uYHKpYwU3ms5BGQQgah7oAcV/B7D4KaZfbCx1IHRoCA3a1RtE0TKYapq5TYluu5VBNJQW9WFBNh9kapZqt2kXHpUbBJJZj11XLdGyq1gnV2MEtgbD1oOe7cYC2B+zX6qGqiOdGwW16DzoNMCSp6KCDnBrUYZWgFYTgdg1GDBKrtPjBuAL7gc+AuMFo1Au5FFEfMo9A6jLahs1l4rAsTYCGDyCC5yn1vUbQcgH1/F9+cOfjmyRinGO912iw8Mpjd7ikK9gBXyiF2U+SmDAxhiYaETZngedHE4aFoSEx+n0w7TqQIS6pQuIHe4T83FiCKtwoQwSVY92fsW0YRDSKm3p9oB5aqqWpplXUtKJZMCxTGdU2++qGx6EtljRA1RRpjYPxECJaJ/TaXuTdCkijKPTqvSg5/Vnt4vjyuFaN06nWzk+PTuDwcCDPd724iATIIHMoTj9mpg6ou0kzx4ui8qK0O9BOP7p86IBLQSOmVClobazGV98qOnRs0Rs0Hkb7tzBZdzLNfXQ3dHPoflE5Ojk6x/zQIwL52PGR6w8RqzUa8ZQKeML8ZgQdJsUiv81hk7/TDdsko2QmfGPjZ3sX4LqMeVtk3Fmf42wamdA2kPm0zYZfLYfJRM3hsAxjnEOzSZYkmbk5JLZuTqnBJIkYylFnRFfk8ITwC7u/v/9nBaEvx1U0/VMup4KsTPSDzyNVKg/1ZUlflngy9tlYMo8cV+acyGEK/4QvmsGTiZXNQeYf/OYEgmcROY07WExemDeVB8+IMcj4z5PRjPpNlXOI/zrn0DLsq2gFrcG6htbRFtpAm7DmYX+FXqBtWLfRS/QW7aDXsL6B/T16h3Zh3UV7wJB81sS+JOQUL8OzLWG4vsAxwvDXAH2QMN8/Sjgn8ebEviThZcGZ4hXhm2LOvyFh/i4v4XWRX4q57UsJb8KzI+G8yD/FW/DsSphz7Un4iTmdcp9nze/C84KemIsZ8/7U3P2f74G/nwjxdw==', 'selectors': 'eNqtWFuP6jYQ/i95hlXskAD7enpRVamVeo7OywpV3jCA25AgJ6S7OuK/d2Zi5w5kxWqVLInj8ffN3f7h5fEBjuo7mFxnqfcsZt7JZDudgPfsKVPonYoLb+ZBWpj333W6xdcnfI+vYnVSrzrRhYbce/7hwXYPXxKV53qnY1WwuB+eSvaZ0cXhiBOzeP5q4DSPs7TQ6VkX7/NSoCSV7s+JMt+yBIxKY/gJ9t6znHm5Op4SEh5c8OFsEAv8jKv8BekWjE73/QUIwjzRKeSV4FLnmiG+MzAS9eLtQBVnAzhc4MJIDH/loI74bwv4jBgK8Da4JOJMIS5g+xUS/G8ZMc+D0pae3qdHFMKYgyf/Uov9BdHigoU5wwXfvr5mbzTjSBNf/Cd/Zq/NzDsqHHoR+CzxQim0el6oghWbxfHZGEDFfMnOCJeMlIDa/TnyPj+oU+uRNGafolmF3I6hekswBbzZF6tLNd7R7SP6G2ifF2JCTgKiuLC+XhMYME3Ox5TX01uUfFLFAf+l6kgL59nZxPBH9YDuiCB+o68KdJ98lxnCwvq2CvlaVC7b0k6lGzfQ6KnSgnvfaMypy420lbepJXdBJ1msEgbWEKsezVanKsFf/1JIjfOyBGIkB4aixICihWkR+DD+jftoIkQm1ANrQ/Db+wkaPDW+FPXOn1ng1/RoMJhKCnPCnah9XtlQHfPGpX412flEmKuJD2DGD0qHOIF0z25UY7ZQr6nyusnv0FDbf1Rsc4Cb0GQ9ihR9gK1RCaWNQaTQx5xgNs2ij6igx/aKg1zawmipFy+jJIpXek6S1o0zld+7PvBuM5ueBnECXhH9l3hbbVywOYRPOYHkG8GlHJglpKQG7/TV8JOQUYuncDOTNBDRBAtgY6OoXntHy+7c2g4NFdFEpegZNPnFaYJlvszp57yGcQub39PEYhYRFsyTRu9HZqk3nVtKvl0E5wlclLFKwip7WGUXq+hg7UiqoYq7alyMQBUToEoHNSCoQQ9qUEMNKrWG1qEIKksRA6zXTO7XWFeENpyg1nnLo3Fe4LAuCOuih3UxxCobF2hL6kCVN9WKETCOVd7BunBYQ8Ia9rCGNVbpsNbgKklzp91pkWv9LkK0wWqCZtup4TILHdiIwEY9sNE42IDB3swxwVW9Sv8G0uA60mhjK1SdD4CQwkg+oKa09gW/8dt74e+3wl/ytQhm0eK6SrfauFa1h7ZuqLd/H5TBBLmmsaadc3kCKEXASJ6oOMh2SgvuKbqbwqT9C5ZXOAQDDj1PvkfCZRCg5AEjGaRnCDlmCHkzDy/ICNcMIT/BEC6zACUVGMksI4aYXEsqNUYIIrplCP9RQ7iUA5RtYCTltAwhxiJCTMjckq+BIcQnRYTLREBJCEYyUcsQYiwixN3UIyik+TYwhfismIgcjSXRWPZoLAemkGOmuFuZZHUbNcZnRMXSsVgRi1WPxWrUGJPbFluxKKz5dtUYD8fFytFYE411j8ba0RDtDmdyR1MXXlnd5GpyePc7sTs01o6G8Lni+f2S548yCaYU5ibCBYU3364yCR5lIvyaSlW8B9VbDKjIj/RuvusxpL2tJteNj1KpS7jgGi76RVzIUSrBGJUb7RIXcXmLyuNW4b0WbX51a7tVHeoN26uuN9zrR9o9lau1JamqHDY93YiZ0q3Z3UEjmZqQctiJdHV1S/vd9iNqJFNrUA77g65DTWlsKszrRjLV63JYtLs16V6V89vloRZNZbQc1tJuhp3SBNhkFzaiqbSVw/rWcd9BUbtRm1eNaKo35bDodKvblHpp3V+g6A0fIvFJeXUs6o63s/94r4GOjXpDg2IxQuXTRnVNxxDcMghyBPq9xAGBAyGHP29H6NSpFkK7BtoyBnyAQCIFfUuT61Y25GUCanhYwHc+dLIi6PyFGxSeih9V23uLasnieAOFvyU1G/bMqkdlzRhWFYKKmaBpIY4smEWIApnXkpLy5fI/nqyjvA=='}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'ed085edcb64085e841fec251451ac621ec0789496dc57f816d1eadc03b56df33', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFE8mrlzzrl3ztzEO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZBAUUjbg93U1sBTFNRgpAibAXAcuoHgdOA3ccw0NtgH2dSheaiNC8N8acgiLOzLzX7W4G2KvTmTEfM9yMWIKM8iHZTAg5xkyG8w5BJl3UQb5sAwsYhbAcA7mDRlp1ht4ho/R6GIYjQ+PqQsn45Nxb3oejofzkezP3fn0LOyT+16bzJdnsIu7usSosJXWExQ+BXsgTpDp9uQ2FCUu3AA4IE6QsSkzQIY4dcg5h3m97udwQg47bHu19z9//fj759sbPWp8UI5w6NM0RC5TWURWEeksJvS4riM4/9GiSy93naJ481PMPUnerMYgl01Ko55JBG6J1UiLZfouMYtS42NMQaxJoxB6jXomKlWF1UiLVXqVmcW8xofQmGLW76cC1FhmWqzTd4VZlMwgVCdYWSYvHD+ZcR4h5gEG9xFcFlKnBHeWjruHmGy5g7SWNQbPRC7ANQejT5Jqv0GvKrfoCQuD38HcgSmbIJdNSqNeBp3SJquRFhvCwuC3MAWxJo3xo9NY0xJRqbZYjbS4LSwMLjXuQ2OKuc7k6O6wzLS4KywMDkJ1gtLgt5MZ5yZi9jC4gWBlcCUjMfiuNpUNTm0Myba5PZtUkIG6qylmjwPfX8cc5260mLwGsofKIm4nM7KaYgsDH8GqiEpGUsSGNpVNTm3sI7Ybxgrs/aBvtQ3tdgI/WMcc18f+nqLbqyLi6BxpQGkj9NyVIioZSRFxfRQgGrQyBRwtcGArgKpoCSC8rgDxG6D8LS8NznsFUNUiAYTkZgmAdxGxg0h56OhDqrEDr+ni3wyM1JS/f/8AfxceIg==', 'glb': 'eNq1Vt1v2jAQN/0u7bZu67o9TZWf9pCixAmB8DI6tkmdWqjaqi8VDyZxWDRIUAj9UIXEf7bX/Vk7JwZM+CirNCTb97Pvfnfn8wHN1tX3FYTQpyxCX9cR+nFZqz5i2u2yCJce8S0Lu17g4xImORUruMl8FtIoCGHHpg5A3Fcwu49C2uX6Yg+kDg2BAWu27jDLzVvEyNO8UWDEMm2t4bKCq9vMcEgx7zi6S2ymsmJDbVCLMNcuarbj2Hndso9uCbhtBD3fiR20PWC/UXOqIkZdwW16D3sQoMKHDnsQk0ttVglaQQhmN6DEILBKiyfGN7Af+AyIXUajXsiliPoQeQRSl9E2LA4TybJhADR8ABEsz6jvuUHLAdTzf/nBnY/ricc4xkbPdVl47bE7XNIVbIMtXIXZT4KYUjFGKhoROueB50dTivmRIjH6fVDt2hAhLqlC4ok9QnxOLMEt1JURgptj3Z+xbhhENIqLenOk5gpqQVPNQlHTiibUx1TGd5s+qnM/tMWSAqiaIs2xM+5CeOuEXtuLvFsBaRSFXqMXJdmf1y5Prk5q1Ticau3i7PgUkoeEPN/x4kskQAaRw+X0Y2Zqw3Y3Kebkpaj8UtodKKcfXT10wCSvEVO6KShtvI2vv1V0qNiyL2jSjfZvbtLmZJb5+G3o5sj8snJ8enyBedJjAjntOOXGQ8Rqrht3qYCnzG9GUGFSLPLXHDb5mW5YJhkHM2UbKz/bOg/PZcK6QCaN9QXGppFybQGZT9ts9NWSSzpqAUfBMCY5NIukSZKeW0Bi6eaMO5gmEU05royoiuyeEP5gDw8P/6wh9OWkimZ/yuWhIG+K/dTeSF8+K0v6aFp/yld5jt/yghhm8adt0RyelK+pGCT+wW+QB8nZUvJnwTtYTl6adygPnuFjkLJfJKNUHgvlDOK/zhm0Cus6WkMbMG+gTbSDttA2zFlYX6EXaBfmXfQSvUV76DXMb2B9j96hfZj30QEwJJ8Nsa4IeYhXYexKGJ4vcIwx/DVAHyTM148Szki8GbGuSHhVcA7xmrAdYs6/JWF+lpXwpohviLnuSwlvw9iTcFbEP8Q7MPYlzLkOJPxEn856z/P6d9l+QYv7Ym6/P9F3/+d74C8SM+eH', 'selectors': 'eNqtWFuPqzYQ/i88JyswgST7enpRVamVeo7OSxRVXpgkbglEhtBdHeW/d2aMuSchymoFWTCe+b4Zz8X+4eTRAY7yO+hcZanz6s2ck852KgHn1ZG6UDsZFc7MgbTQH7+rNMbXJ3yPryJ5km8qUYWC3Hn94UC8hy+JzHO1U5EsWNwPRyb7TKvicMSJWTR/03CaR1laqPSsio956aEkme7PidTfsgS0TCP4CfbOq5g5uTyeEhLuX/DhrBEL/Ixa/oI0Bq3SfV8BQZgnKoXcCC5VrhjiBwMjURtnB7I4a8DhAhUjMfwvB3nEnxjwGTEU4GxRJeJMISog/goJ/laMmOdBqoqe2qdHFMKY/Rf3Uov9BdGiwkKf4YJv396yd5pxpIkb98WdVdd25hwlDm0EPtOFUkh7XsiCDZtF0VlrQMN8yc4Il5yUgNz9OfI+P8hT65EsVj2FM4O8GkPzlqALeK9erC5mvGPbZ+w3sD4rYkJWAqK4sL3eEhgwTc7HlPWpGCWfZHHAn1QeSXGenXUEf5gHXI4I4jf6qsDlk+8yTVjY3pVBvhZmybasY2xjBxo7GSvY943FrLnsSNt421pyF3SSRTJhYA0x86hjlcoE//uXQmqcV0UgQnKgKUo0SFJMSuBh/Fv70USITKgHtgrBbx8naPDU+FK0O39WAb9mR43BVFKYE+5E7nPjQ3nMmyX1q87OJ8JsJj6BGT8oLeIE0j0voxpzBfWaKa+7/A4NGf8joyoH2AlN1qNIUQeItUwobQwihT7mBLNtlD5jgh7bKwvk0hZGqjZORkkUr/ScJK3bxmuyV3098G47m54GcQJeIf0KvK22NtgswpecQPKN4FIOzBIyUoN3ujZmZq5gO/NpwKObRbCtwqhWviO9O6vcwqEqmsgUl0aIkzduW+hmTv/Oaxy3wLk9UyxmAVkDE6VW+5FZ8l0RMmt2UoLzPFTKWAVhFT2sootVdLB2JNVQxV07LkagiglQhYXqE1S/B9XvQvXqFUVQWYo3wHrN526NdTXVrPPWksZ5vsW6IKyLHtbFEKtolkBbUgequGlWDIFxrOIO1oXFGhDWoIc1qLEuLNYanJE0t9adFrrVugtpHfgTLNvODZdZYMGGBDbsgQ3HwfoM9maS8a/aVbg3kPrXkYbbqkTV+QAIKYzkA+pKHZLVBhncD3+3Ff6Cr4U3CxfXTRorbXvVHtq6o47/PkiNGXJNY00/Z/MEUIqAkTxhOIh2SvPvGbqbwkT15wdXOPgDDr2VfI+EzSBAyQNGMkjPEWLMEeJmHl6QE645QnyCI2xmAUoqMJJZRhwxuZYYM4YIIrzlCPdZR9iUA5RtYCTltBwhxiJCTMjcgq+BI8QnRYTNREBJCEYyUcsRYiwixN3U41FI823gCvFZMRFaGkuisezRWA5cIcZccbcyCXMbdcZnRMXSslgRi1WPxWrUGZPblqpiUVjz7aozno6LlaWxJhrrHo11h4bXVjqlo6kLrzC3B8K734ndobG2NDyXK57bL3nuKBN/SmFuItyj8ObbAxXjQSaeW1MxxXtQvb0BFfFI7+baHkOY2ygV8RlO8eoS7nEN9/pF3BOjVPwxKjfaJS7i4haV573Cey3a/arWdsuc6g3bq+5quNePtHsqW2tLMlU5bHq6ETOlW6t2B41kakLKYSfStdUt63fbj7CRTK1BOewPugtqSmNjMK8byVSvy2HR7take1XObZeHWjSV0XJYS7sZdkoTUCW7oBFNpa0c1rfO8h0UtRu1edWIpnpTDotOt7pNqZfV8vdQ9JZPkfio3JyL2vPt7D/ea+DCRruhQ5don4A3qms6huCWwaOFQP8vccDDgYDDn7cjdOxUC6FdQ0ArnQ8QSKRH39LkupUNWI1PDQ8L+M6nTpUI2m9y3uKp+BFv78MK1ZLFuTx9SV1lfWjVo7JmDCuDwDDz+EgDRxbMApOJ4bWkpHy5/A8IyqPZ'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': 'bca29d8e668cef75b9fa10687b2f4b3eb1535e5c4b11619bd55255a24e503011', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFU8mrlzz7lnzlzXO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZDKoTNmA39flwEIU52CCQLnFdWADCuvAcuCea3CwDbDXobDU5gzBf3PIZVjckZn/qsXdEHt2IkPmexaLIVMYQT4cAqcHi5DdYKxBBhiQWAT5cAh8xmwCwzmYN2SoWW/gET5Go4thND48pi6cjE/Gvel5OB7OR7I/d+fTs7BP7nttMl8ewS7u6hKjwlZaD1D4FOwTcYRMtye3oShw4QbAJeIIGbsVk8gQpw455zCv1/0cTshhh22v9v7nrx9//3x7o88as3KEQ39NQ+SylEVkFZHOYkKf13UE5z9adOnlrmMUb36KuSfJm+UY5KpJcdQjicAtsRxpsUy/JWZRcnyMKZA1cRRCz1GPRFJVWI60WKVXmVnMc3wIjilk/X4SoMYi02KdfivMokQGoDrByrJ44fjJjPMIMQ8wuI/gspA8ZXJn6bh7iMnIrfum6Q2uu5C6g9EXSdpv0KvKLXrCwuB3MHdgqibIVZPiqKdBp7TJcqTFhrAw+C1MgayJY/zoONa0QCTVFsuRFreFhcElx31wTCHXmRrdHRaZFneFhcEBqE5QGvx2MuPcRMweBjcQrAyuaCQG39WWsqHlQzrFKTm/uXtsKSsiogJ1V1PIHpd8fx1yXDvX2fLXQPZQKeJ2MiPVFFsY+AhWIioaiYgNbSmbHNvYR5zZ3JiBUUTpB32rbTD73YN1yLE+RhHlHUC3VyLi6BxpQGkj9NwVERWNRERcH5UQDVqZAo4WOLCVhEq0JCG8rhLiG6D8LS8NznslodIiSQjKzRIS3kXEDiLloaMPqcaOfE0X/83ASE35/fsHBFke6g==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIbx6XktJWSpWEKIlyiTis7TW1CjayTR6KkPhnvfZnddZeYLEJoZGK5N35xjPfPHYHaHcuvy8hhD4VEfq6itCPi+bpAyZRRGNce8A3NIy8wMc1rJZkLOE29WlI4iAEjU0cgHggYXoXhyRi9lwHUo+EwIA1xzINw7ENgyi6ajmuaToVs+JWXUMHFZFNhRLbIZZMqzbRHFVxK7JOjIpiqWXbNg5uVAhrBX3fSQJ0PWC/lkuyxJ+WhLvkDnQaYEhSgr0FObnEpo2gE4Tgdg1GFBJrdFhhTIH9wKdA7FIS90MmxcSHzGOQIkq6sDmUF0tHCZDwHkTwPCG+5wYdB1Df/+UHtz5upRGTHK2+69LwyqO3uKZJ2AZfaIU5SJPImehjE0XlNmeB58c5Q2NsqOqDAZhGNmSIazKXWGEPkJ+TSNCFljRG0Dka/UxswyAmcXKo1wdyqSyXFdksVxSlYhp62ZQmvc2+arE4pEPTA5AVSViTYCwEj9YLva4XezcckjgOPasfp9WfNS+OLo+ap0k6p83zk8NjKB4K8nzHS5qoAhlkDs0ZJMzEBnWUHuZ0U2TWlG4PjtOPL+974GIoqil0Co42UeOrbw0NTmzRGzQdRvm3MFl3dZb75G5o5tj9onF4fHiOWdETArHspGTrPqZN102mlMNj6rdjOGG1UmG3OWyzd5peNdVJMjnfxPjZ3gZclynvsjrtrM1xNvVM6CqQ+aRLx18tpXSi5nCUdX2aQ6mqWZJ05uaQVDVzRg/yJHwoJyfDT0UMr6rswu7v7/9ZQejL0Sma/anXR4KoTPX1et5urBcwythO8WZ0Io8YV+TM5VCfzYfq+Vg5nkysbA4i//A3yMP03ULyZ847XExemHckD58RY5jxnyejTB1z5QJiv84FtAz7KlpBa7CuoXW0hTbQJqxF2F+hF2gb1m30Er1FO+g1rG9gf4/eoV1Yd9EeMKSfNb4vcXmEl+HZFjBcX+CYYPhrgD4ImO0fBVwQeAt8XxLwMucc4RXuO8KMf0PA7F1RwOs8vxFmti8FvAnPjoCLPP8R3oJnV8CMa0/AT8zpjPv82PwuPC9o/lw8Nu9Pzd3/+R74C5aC7N0=', 'selectors': 'eNqtWFuPqkgQ/i884wS6BXVez16y2WQ32XNyXozZ9GCpvYtgGmRncuJ/36qC5q5inBhAaLr6++pO/3Cy6ABH9R1MptPEefVd52TSnY7BeXWUyfVORbnjOpDk5uN3nWzx8Qmf46NIndSbjnWuIXNefziw3cOXWGWZ3ulI5Szuh6PifWp0fjjixDSavRk4zaI0yXVy1vnHrPBRkkr251iZb2kMRiUR/AR751W4TqaOp5iEywvenA1igZ9xlb8g2YLRyb6/AEGYxTqBrBRc6EwzxA8GRqLWzg5UfjaAwzkujMTwXwbqiJct4D1iyMHZ4JKIM4Eoh+1XiPFaMWKeB6UrenqfHFEIY5Yv3qUW+wuixQVzc4YLPn17S99pxpEmrr0Xz62OjescFQ6tcbYr8MArrZ7lKmfFplF0NgZQMV/SM8IlI8Wgdn+OPM8O6tS6JY1Vd6FbIq/GUL0FmBzeqwfLSzne0e0z+htonxdiQlYCoriwvt5iGDCNz8eE19NblHxS+QEviTrSwll6NhH8Ud6gOyKI3+itHN0n26WGsLC+K4V8zUuXbWmn1I0daPRUasE+bzRm1WVH2srb1JK7oOM0UjEDa4iVt2arExXjv38ppMZ5VQQiJAeGosSAooVpEXgY/8a+NBEiE+qBrULw28cJGjw1vgT1zq9VwK/p0WAwFRTmhDtW+6y0oTpmjUv9atLziTCXE5/AjC8UFnEMyZ7dqMZcQb2myusmv0NDbf9RUZUD7IQm61Gk6ANsjYopbQwihV7mBLNpFn1GBT22Vxzk0hZGS62dlJIoHsk5jluntd9kr/p44NnGnZ4GcQIeIV0FnpYbG2wW4UtGIPlEcCkHpjEpqcE7fTVkFjBqvG7cOQ34S5pRIdhUYVQvvqN1d3ZxC4eqaKwSdI0QJ6+tKljoekZ/ZzWOW+C8nirm7pxwYKI0ej8yS71rQmbVTovgPB8XZayCsIoeVtHFKjtYO5JqqPKuHucjUOUEqMJClQRV9qDKGuqKoJKtvBoqS/EHWK/Z3KuxLhFrMJ+g1lnLpXGetFjnhHXewzofYhWNC7QldaCKm2rFEBjHKu5gnVusAWENeliDrgvYGGCBpaSZ1e600K38LpzqsO3ccHEDCzYksGEPbDgOVjLYm0lGXtWruBVa8jrScFOVqDofACGFkXxAXalDstogg/vh77XCX/AhV244v67SrTa2V+2hrTvq7d8HZTBDrmis6edsngBKETCSJ0oOop3S5D1Fd1OYqH5SXuEgBxx6nnyPhM0gQMkDRjJIzxBizBDiZh6eu4KPUUOITzCEzSxASQVGMsuIISbXklKNIYIIbxnCe9YQNuUAZRsYSTktQ8ixiJATMrfgY2AI+UkRYTMRUBKCkUzUMoQciwh5N/X4FNJ8GphCflZMhJbGgmgsejQWA1OIMVPcrUyiPI0a4zOiYmFZLInFssdiOWqMyW1LVbEorPl01RhPx8XS0lgRjVWPxqpjDNvhTO5o6sIrytMDtaLfid2hsbI0fI8rntcved4oEzmlMDcR7lN4+8tbTOSzTHyvplIW70H19gdUxCO9m2d7DFGeHqgbj1KpS7jPNdzvF3FfjFKRY1RutEtcxMUtKs9bhb+16OtXtz63yl29YXvV9YZ7/Ui7p7K1tiBVFcOmpxsxU7q16uugkUxNSDHsRLq6uqX9bvsRNpKpNSiG/UHXoaY0NiXmVSOZ6nUxLNrdmnSvynnt8lCLpjJaDGtpN8NOaQKqZBc0oqm0FcP61nHfQVG7UZuXjWiqN8Ww6HSr25R6Wbm/j6I3vIvEW+Xlvqjd307/428NdGzUGxp0gfoJ+EN1RdsQ3DL45Aj0f4EDPg4EHP78OULbTrUQ+moIyNN5A4FE+vQuTa5b2YCXkdTwsIDvvOtUiaANGM5bPBVf4s/7sEK1YHEeT19QV1lvWvWorBjDskRQMvNpWoAjc2YRoEDmtaCkfLn8D/x7pH4='}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'cbccb6aabd344d5ca5314286fa349e93ba29d7162354f7b55790f62395ac9f9d', 'payload': 'eJylVs1u00AQXju/ddPWtED5x3CCiAOkEUdwlSJxQCpSIu5W2oZKURwlBKmceA5eghdAKm/Ba1Q8AePEM5vYM9l1sap4tTM73zcz347rHY9PR0HnsNs5PHoX9OJxPIwHF8Gn9ovgWf95sLBG0350cup9iPvRl/N4NA1eep3Z5Otp62QatNqeo9jn6i2ubB3qoeB4KUTQkbIOv6/LQYQozsEE4YeCg7EOvuRQuA4iB+m5BgdbB/s6FC61OUL43xxyEeZ3ZBq8akk3xJ6dypD5nsUSyBRGoEdCkNouImQPGHOgAwYkEYEeCUGOmA1g6IP5QIaa9QEZ4WM8vBjEo4MjmMLp+njUm5xHo8FsSPO5O5ucRX1Q32uT+PIIdn5Xl7gqLKX1AIW7YB9IImS6PbkDRYELDwApkETIOJSFQAY/3eScwrxe93M0BoUdtL3a+5+/fvz98+0NHzVplKMc+GsaPBepzD2r6OnMN/i4rqMk/YHRhZe7jlFy+CnuPUnfIscwl80KRx5JhW5J5AjGMvyWBCNxfIxbSNbEUSmeI48EpaqIHMFYhVdZMOY5PkSOK8j8eShATUQGYx1+K4KRkBFQd7CySF45QbrjPEKfB7i4j85lRTwpuLNQ3D30yZSb+6bxAucuJNcYPkmo/Qa8qpLRUxYCv4N7+6Zswlw2Kxx5GtClTZEjGBvKQuC3cAvJmjgmD8exxgJBqbZEjmDcVhYCJ457yHEFuS7k6O6IyGD0lYXAEVB3kAR+O91xbqLPLi5uoLMWuKaRCtxnU9lg+fihm4Tk9QbGXTEVX6eCGei7uoLsScH31iEnufMSW0Kma0AzlIq4ne5QNdUWLgJ01kXUNNIiNthUNiW2iY54sYExYWAsIumBH7UN4by7vw45qY+xiHQHcNrrImLrHBIgyQhn7lIRNY20iHh9dEAc0FoUqGiFDVsKqIuWBkSt64D4DdD6pkuD/V4KqGuRBkTKzRIGvIseO+hJTcc5pAc7xmu6+N8MCqlJ379/iYwfsg==', 'glb': 'eNq1VktP20AQXt4QaEtbSnuq0J56MJG9fsW5FJq2EhUQBIgLymFjr6nVxI4ch4dQJP5Zr/1ZnbXXycYJIUVqJO/ON5755rE7Sa5a59/nEUKfSgh9XULox1n9+B7TbpcluHqPr1ncDaIQVzEpq1jBVyxkMU2iGDQu9QDivoLZbRLTLrcXOpA6NAYGXDFck3mu4du+axjEqdjMIcxQHd/SiWZ6mmk4quo41DXVpq/rnkqNZtO03YrGPOKQ3WsCYZtRL/TSAO0A2C/VsqqIp6HgNr0FnQEYklR00EFOPnVZLWpFMbhdghGDxGotXhhX4DAKGRD7jCa9mEsJDSHzBKQuo23YPCaKZXkCNL4DETyPaBj4UcsD1At/hdFNiBtZxDTHZs/3WXwRsBtc1RXsgi+0wupnSYyZGAMTjQibkygIkzFDc2BIjH4fTLsuZIirqpB4YfeQn5dK0IWGMkDQOdb9mdrGUUKT9FAvd9WyrdqaatkVTatYpmFbyrC3xVcNHoe2WHYAqqZIaxqMhxDROnHQDpLgWkCaJHHQ7CVZ9Sf1s4Pzg/pxms5x/fRo/xCKh4KC0AvSJhIgg8yhOf2Umbqg7maHOdoUlTel3YHjDJPzuw64mBqxpE7B0aZqfPGtpsOJzXqDRsNo/xam6E4muQ/vhm4N3M9q+4f7p5gXPSSQy05Lbt4lrO776ZQKeMjCqwROmFQq/DbHV/ydbjgWGSYz5psaP9vbhOsy4m2TUWd9irNlFEI7QBbSNht8tZSziZrCYRvGKIfmkCJJNnNTSBzdmtCDcRIxlMOTEacihyeEX9idnZ0/iwh9OThGkz97e7kgKzP9g6TL5YF+T9LvSTwF+2IsmUeOK3OO5TCBf8wXPcJTiFXMQeZ/+M0JBM8s8uc86GzyzLy5/PCMGA8F/2kyKtQxVZ5D/Nd5Di3AvoQW0TKsy2gFraNVtAZrCfZX6AXagHUDvURv0SZ6Desb2N+jd2gL1i20DQzZZ1ns80LO8QI8GxKG6wscQwx/DdAHCfP9o4TnJN45sc9LeEFw5nhR+OaY869KmL8rSXhF5JdjbvtSwmvwbEq4JPLP8To8WxLmXNsSfmJOJ9znx+Z35nlBT8zFI/P+1Nz9n++Bv7qs8ds=', 'selectors': 'eNqtWFuvozYQ/i88kyMwlyTndXtRVamVuqt9iaLKh0wStwQiQ+gerfLfOzNgrk7CKkcREDCe+b4ZzwV/d4rkCCf5FXSh8sx59V3nrPO9SsF5daQu1V4mpeM6kJX6/XeV7fDxGZ/jo0Se5ZtKVamgcF6/O7A7wKdUFoXaq0SWLO67I9NDrlV5POHEPFm8aTgvkjwrVXZR5fui8lGSzA6XVOoveQpaZgn8BAfnVbhOIU/nlIQHV7y5aMQCP6OWvyDbgVbZYayAICxSlUFRC65UoRjiOwMjURtnD7K8aMDhEhUjMfxXgDzhZQd4jxhKcLaoEnFmkJSw+wwpXhtGzPMoVUNPHbITCmHMwYt3bcX+gmhRYakvcMWnb2/5N5pxookb78Vzm2PrOieJQ5sQ7wUeKIW0F6Us2bB5kly0BjTMp/yCcMlJKcj9n5bnxVGee7dkseYudmvkzRiatwJdwrfmwepajw9s+4z9JtZnRUzISEAUV7bXWwoTpunllLE+tUPJZ1ke8ZLJEyku8otO4I/6BpcjgviN3ipx+RT7XBMWtndjkM9lvWR71qltYwY6O9VWMM87ixlzmZG+8bat5CHoNE9kysA6YvWt3qlMpvjvXwopO6+GQILkQFOUaJCkmJTAD+PfmpdmQmRCI7BNCH55P0OHp8WXod35tQb4LTtqDKaKwpxwp/JQ1D6Up6JbUr/q/HImzPXEJzDjC5VBnEJ24GXUYm6g3jLlbZc/oCF3/8ikyQFmQpf1KFLUEXZappQ2JpFCL3OC2XZKnzHBiO2NBXLtCyNVGyenJIpHdknT3mnjd9mrPX7g2dadnwZxAh4xXQWeVlsTbAbhS0Eg+URwKQfmKRmpwztf20Y0qP2XaOtGdCfoDYNg24RRq3xPevdGuYFDVTSVGS6NGCdvvL7QzYL+Lloc98B5I1OEbkhGwESp1cEyS35ThMyYnZTgPB+VMlZBWMUIqxhiDQdYB5JaqOFDO4YWqOEMqMJADQhqMIIatFB9UsXO8lqsLMafgL3ldK8Fu0KwUTzDrovemsZ5gQEbEthwBDa0gBXdIuiLGmAVdw2LQWAHKx6ADQ3YiMBGI7BRC3ZlsLbgakkLY955wdusvJjWwXKGafvZ4epGBmxMYOMR2NgONmCwd9NMcNOuwruDNLiNNN42RarNCEBIwZIRqC91SFYfZPQ4AXi9BCD4CJZuHN426U5p062O0LY99e7vo9SYI9c01nV0JlMAJQmwZIqag+gnteCRoYdJTDS/QNzgEEw4jFbyIxImhwClD7DkkJEjhM0R4m4mDl3Bh9UR4gMcYVILUFYBS2qxOGJ2NanNGCOI+J4jvGcdYVIOULYBS8rpOSK0RUQ4I3ULPiaOCD8oIkwmAkpCYMlEPUeEtogIH6Yen0KaTxNXhB8VE7GhsSQayxGN5cQVwuaKh5VJ1CerMz4iKpaGxYpYrEYsVlZnzG5cmopFYc2nm854Oi5WhsaaaKxHNNaGRthvcWa3NG3hFfUp9GeH97gXe0BjbWj4Hlc8b1zyPCuTYE5h7iLcp/Dm000mwbNMfK+lUhfvSfX2J1TEj/RunukxRH2yUhEf4RS/LeE+13B/XMR9YaUS2KjcaZe4iIt7VJ73Cn9t0fev6n1w1ft60/ZquBoe9SP9nsrU2opMVU2bnmHEzOnWms+DTjI1IdW0Exna6p71h+1H3Emm1qCa9gfDBTWnsakxrzvJVK+radEe1qRHVc7rl4dWNJXRalpLhxl2ThPQJLuoE02lrZrWt8HynRS1O7V51YmmelNNi86wus2pl83y91H0lveReLO83hk1O9z5f/ytgQsb7YYOxZKKxqcv1TVtRHDL4NNCoP9LHPBxIOLw588R2nhqhdBXQ0QrnbcQSKRP79LktpWNWE1ADQ8L+Mr7To0I2oLhBoWn4kv8gR83qJYszuPpS+oq222rEZU1Y1jVCGpmPk2LcCRkFhEKZF5LSsrX6/9zc6S1'}]


def seed_result(
    document: Path | str,
    descriptor: Mapping[str, Any] | None = None,
    *,
    model: Path | str | None = None,
    parts: bool = True,
    components: tuple[str, ...] = ("c0",),
    kind: str = "assembly-package",
    entry_kind: str = "part",
    sidecar: Mapping[str, Any] | None = None,
) -> str:
    """Seed geometry and, unless ``parts`` is False, each component's part as a build
    derives it under FIXTURE_PRODUCER (its display mesh, selector table and
    record), without native imports.

    Caller aliases are remapped to genuine geometry CIDs. Explicit malformed
    structure/geometry overrides stay malformed so completeness checks can reject them.
    """
    import base64
    import copy
    import zlib
    from cadgen._internal.component_package import canonical_json_bytes
    from cadgen.store.meshes import body_facts
    from cadgen.store.objects import put_object
    from cadgen.store.parts import _write, part_key
    from cadgen.store.records import note_document_tree, note_output, write_record
    from cadgen.store.trees import TREE_KIND, TREE_SCHEMA, tree_kind

    def inflate(text: str) -> bytes:
        return zlib.decompress(base64.b64decode(text))

    document = Path(document)
    descriptor = copy.deepcopy(dict(descriptor or {}))
    raw_components = descriptor.get("components")
    if not isinstance(raw_components, dict):
        raw_components = {cid: {} for cid in components}
    if len(raw_components) > len(_GEOMETRY_FIXTURES):
        raise ValueError("seed_result supports at most four independent fixture components")
    tree_components, aliases, component_colors = {}, {}, {}
    for index, (alias, raw_entry) in enumerate(raw_components.items()):
        fixture = _GEOMETRY_FIXTURES[index]
        entry = {name: copy.deepcopy(fixture[name]) for name in ("kind", "codec", "brep", "faceColors", "contentHash")}
        put_object(inflate(fixture["payload"]))
        if parts:
            key, glb = part_key(entry, FIXTURE_PRODUCER), inflate(fixture["glb"])
            if body_facts(glb)["part"] != key:
                raise ValueError("a seeded part's mesh names another part: derive the fixture parts again")
            _write(key, glb, inflate(fixture["selectors"]))
        raw_entry = dict(raw_entry or {})
        if raw_entry.get("color") is not None:
            entry["color"] = raw_entry["color"]
            component_colors[alias] = raw_entry["color"]
        if "brepObject" in raw_entry:
            entry["brep"] = raw_entry["brepObject"]
        cid = entry["contentHash"][:16]
        aliases[alias] = cid
        tree_components[cid] = entry
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    occurrences = descriptor.get("occurrences")
    if not isinstance(occurrences, list):
        occurrences = [{"id": f"o1.{i}" if len(aliases) > 1 else "o1", "name": alias,
                        "component": cid, "transform": identity}
                       for i, (alias, cid) in enumerate(aliases.items(), 1)]
    else:
        for occurrence in occurrences:
            occurrence["component"] = aliases.get(occurrence.get("component"), occurrence.get("component"))
    children = [{"id": occurrence["id"], "name": occurrence.get("name", occurrence["id"]),
                 "nodeType": "part", "children": []} for occurrence in occurrences]
    assembly = descriptor.get("assembly")
    if not isinstance(assembly, dict):
        root = children[0] if len(children) == 1 and children[0]["id"] == "o1" else {
            "id": "o1", "name": descriptor.get("label") or "model", "nodeType": "assembly", "children": children}
        assembly = {"root": root}
    tree = {"kind": TREE_KIND, "schemaVersion": TREE_SCHEMA,
            "label": descriptor.get("label") or "model", "units": "mm",
            "components": tree_components, "occurrences": occurrences, "links": [],
            "assembly": assembly, "stats": {"occurrenceCount": len(occurrences), "linkCount": 0}}
    tree["entryKind"] = tree_kind(tree)
    if descriptor.get("kind") not in (None, kind):
        tree["kind"] = descriptor["kind"]
    for key in ("bbox", "capabilities", "edgeRendering", "color"):
        if key in descriptor:
            tree[key] = descriptor[key]
    # Deliberately malformed fixture knobs must remain malformed; production
    # publication goes through put_tree and native preparation instead.
    tree_hash = put_object(canonical_json_bytes(tree))
    try:
        sha = hashlib.sha256(document.read_bytes()).hexdigest()
    except OSError:
        sha = ""
    owner = Path(model) if model is not None else document
    record = {"entryKind": tree["entryKind"], "sourceKind": "python" if model is not None else "step",
              "tree": tree_hash, "closure": {"hash": sha, "files": [], "static": True}, "children": [],
              "outputs": {str(document.resolve()): {"sha256": sha}}, "stepHash": sha}
    if sidecar:
        record.update(sidecar)
    write_record(owner, record)
    if sha:
        note_document_tree(sha, tree_hash, kind="step", producer=FIXTURE_PRODUCER)
    if model is not None:
        note_output(document, owner)
    return tree_hash
