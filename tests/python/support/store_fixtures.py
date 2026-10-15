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
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': '01e2a28516540a13ff82f3e0479e481eb16c24c2f60cad2cb2e518d90eecee41', 'fingerprint': 'f3a97c046744c0ca9f4c2934560a1dd234453f5e14fd11b4d5bd93caa8f7a681', 'payload': 'eJylVttu00AQXTvXuumFFih3DE8Q8QBpxCN1lSLxgFSkRLxbaRsqRXGUEKTyxHfwE/wAUvsX/EbEFzBOdmYTeyZrB6uKVzuzc87MnB3XOx2eD/zWcbt1fPLe70TDqB/1rvzPzVf+i+5Lf24Nx93w7Nz7GHXDr5fRYOy/9lqT0bfzxtnYbzQ9R7HP9AhXWR2qgeB4LUQwkZION+tyECHyc7BBoCF/HcwJG4ebdTlIzxocsjpkr0PuUtsjBP/NIRVhdkfG/puGdEOys1MJMj+SWAKZ3Aj0SAhS20WE5AFrDrSBjnkR6JEQ5IjJAJY+2A8EeRECG8KnqH/ViwaHJzCF9fp00BldhoPepE/zuT0ZXYRdUN9bm/jSCNn8pte4yi2l1QC5u5A9kETIdntSB/IC5x4AUiCJkHUoC4EsfqbJKYV5nfaXcAgKO2x6lQ+/fv/8++f7Oz5q3ChHOfBXt3jOU5l5ltHTmW3wcV1HSfoDowsvdxWj+PBz3Hum3yLHIJXNEkceSQVuQeQIxiL8FgQjcXyKW0jWxlEpniOPBKUqiRzBWIZXUTCmOT5GjkvI/HkoQEVEBmMVfkuCkZAR0HSwNE9eOb7ecZ6gzyNcPETnoiKeFNyZK+4B+iTKzX3TeIFzF5JrDJ8k1H4DXmXJ6KkMAr+Hewe2bIJUNksceRrQpU2RIxhrKoPA7+AWkrVxjB+OY4UFglJtiRzBuK0yCJw47iPHJeSqkKO7IyKDcVdlEDgCmg6SwO/qHec2+uzh4hY6G4EbGlrgu2wqGyyf6ZEbh+T1BsY9MZW4eToVzMDc1SVkTwq+vwo5zp2X2AIyXQOaoVTEbb1D1VRbuPDR2RTR0NBFrLGpbEpsYx3xYgNjzMBaRNIDP2prwnn3YBVyXB9rEekO4LQ3RcTWOSRAkhHO3IUiGhq6iHh9TEAc0EYUqGiFDVsIaIqmA6LWTUD8Bhh906XBfi8ENLXQAZFyvYAB76PHDnpS03EOmcGO8eou/jeDQqrT9+8fa241eQ==', 'glb': 'eNqdVktP20AQnvB+lUJLaU8V2lMPBtnrV5ILobSVqIAgQFxQDku8plYTGzkOD6FI/LNe+7M662ySdZwAqa3Nzree+eaxO6tcN85/TAHAlyWAb7MAP8+qx4+EtVo8IeVHcsvjVhCFpEzojk40cs1DHrMkinGlzjyEpKMRfp/ErCX05RpKNyxGBmKYluNZlBZNz3Ycq8hKtucXbcZM3fSp7Xtu0aau51kuZ5bLeN3wDdexGK07ll533O1bB91eRe3QSx00A2S/1Hd0TY6aRprsHtcMxBikZuIaxuSzOt+PGlGMZpeoxDGw/YZITCyQMAo5EvucJe1YSAkLMfIEpRZnTZw8LpPlvQBY/IAiWh6xMPCjhoeoHf4Oo7uQ1Loe0xiv2r7P44uA35GyqZE62mIpnE43iJyK1VcxqNQ5iYIwySnafUVqdTqo2qpjhKSsS0kk9ojxeamEVahpfYSV461fqW4cJSxJN/VyW99xddfQHbdoGEXHtlxHG9R2+FNN+GEN3t0A3dCU39SZcCG93cRBM0iCWwlZksTBVTvpZn9SPTs4P6gep+EcV0+P9g4xeUwoCL0gLSJFMowci9NJmVkdl1vdzcwWRRdFad7gdobJ+cMNmtgGdZRK4damy+Ti+76JO/baE5R1Y0zmZticjjIfnA3T6Zuf7e8d7p0SkfSAQE07TfnqIeFV30+7VMJDHl4nuMO0WBSnOb4W30yr5NBBMDnbVPm/rW08Lhlrl2aNzWeM8TLIGpeQLGRN3r9adrod9QyHa1lZDqNEh0m6PfcMScl0RtQgTyKbcrAzcldU95SKA7u1tfV3BuDrwTGMfiqVnpCVxfO0O9Dpyf31Slbu20LWdphH9avyqH7VeMbx5+Ks5P3m8hrKZZj/6Y8g6I5XybvP55uL52lCHzChj6cJfcAkPgowjfM0FGAK5mAeZvCdRWkRlmEBxyIswSq8hRV836C0Du9hDcc6vINN+Agb+H5AqSAZ5+Q8JeUeFl5WFIzHFxkGGP8awCcFi/mzggsKb0HOUwqelpw9PCNte1jwLyhYfFtS8LyMr4eF7qqCF3GsKXhJxt/Dyzg2FCy4NhX8Qp++0LOjzvzIfoHxPZ7rizH9Pu5OGHcPjOvBcb08+h74B2kL8Y8=', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'ed085edcb64085e841fec251451ac621ec0789496dc57f816d1eadc03b56df33', 'fingerprint': 'ae27cb722e97e7b505b2acf5d18df06c2ec8c6a7c889c72fc913b0ffbfe40dfc', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFE8mrlzzrl3ztzEO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZBAUUjbg93U1sBTFNRgpAibAXAcuoHgdOA3ccw0NtgH2dSheaiNC8N8acgiLOzLzX7W4G2KvTmTEfM9yMWIKM8iHZTAg5xkyG8w5BJl3UQb5sAwsYhbAcA7mDRlp1ht4ho/R6GIYjQ+PqQsn45Nxb3oejofzkezP3fn0LOyT+16bzJdnsIu7usSosJXWExQ+BXsgTpDp9uQ2FCUu3AA4IE6QsSkzQIY4dcg5h3m97udwQg47bHu19z9//fj759sbPWp8UI5w6NM0RC5TWURWEeksJvS4riM4/9GiSy93naJ481PMPUnerMYgl01Ko55JBG6J1UiLZfouMYtS42NMQaxJoxB6jXomKlWF1UiLVXqVmcW8xofQmGLW76cC1FhmWqzTd4VZlMwgVCdYWSYvHD+ZcR4h5gEG9xFcFlKnBHeWjruHmGy5g7SWNQbPRC7ANQejT5Jqv0GvKrfoCQuD38HcgSmbIJdNSqNeBp3SJquRFhvCwuC3MAWxJo3xo9NY0xJRqbZYjbS4LSwMLjXuQ2OKuc7k6O6wzLS4KywMDkJ1gtLgt5MZ5yZi9jC4gWBlcCUjMfiuNpUNTm0Myba5PZtUkIG6qylmjwPfX8cc5260mLwGsofKIm4nM7KaYgsDH8GqiEpGUsSGNpVNTm3sI7Ybxgrs/aBvtQ3tdgI/WMcc18f+nqLbqyLi6BxpQGkj9NyVIioZSRFxfRQgGrQyBRwtcGArgKpoCSC8rgDxG6D8LS8NznsFUNUiAYTkZgmAdxGxg0h56OhDqrEDr+ni3wyM1JS/f/8AfxceIg==', 'glb': 'eNqdVktP20AQnoT3oy20lPZUoT31YCK/E+fSUNpKVJAgQFxQDht7Ta0mNnIcHkKR+Ge99md11tkkG5tAKNZm51vPfPPasbhsn/0oAsDnVYBvCwA/Txv1e0K7XZaQ6j25ZnE3iEJSJXpJJQq5ZCGLaRLFeOJSDyHpK4TdJjHtcn1xhtIVjZGBOC2fqYZVaWkty9Yd13A802cesy3VNexyxXBcUzdNy7cMQ9UqpudUHFfVK0y3Pc9yrN1rG922ol7opQ46AbJfqCVVEaupkA69xTMMUOHLwDOMyacu24/aUYxmF6jEMLD9Nk+MH5AwChkS+4wmvZhLCQ0x8gSlLqMd3DwmkmXDAGh8hyJaHtEw8KO2h6gX/g6jm5A0Bx7TGFs932fxecBuSNVQiIu2WAq7Pwgip2KOVDRd6BxHQZjkFK2Rom72+6jadTFCUlWFxBO7x/i8VMIqNJURwsqx7q9UN44SmqRNvdhVS2W1rKnYCE2r2JZZtpVxbbOvmtwPbbNBA1RNkX5TZ9yF8HYVB50gCa4FpEkSB61eMsj+uHF6cHbQqKfh1BsnR3uHmDwmFIRekBZRRzKMHIvTT5mpi8fdQTMni6LyonSusJ1hcnZ3hSaWpttSpbC16TE5/75vYMdmvUGTbrSXucma64+Zj++GYY/MT/f3DvdOCE96TCCnnabcuktYw/fTKRXwkIWXCXZYr1T4bY4v+TvDdGx9HEzONlX+b2sLr8uEdVmfNDaeMLbNjGsHyULaYaNPS2kwUU9wlE1zkkNz9CzJYOaeIHEM+5Ea5EnEUI47I7oiu9d1fmF3dnb+zgN8PajD43+12lCYlOU9Pa/JRhkd2RYyHFkeyHDWptvIOjl+yMeQ44C8rZxLlv/hD8oPgzWT/GXMO4s8M+9Qhhf6eHihD3iJjwLM4T4HBSjCIizBPD4LKK3AGizjWoFVeA1vYB2fVyhtwjvYwLUJb2EbPsAWPu9RKgjGRbEXhTzE3Mu6hPH6IsMY478G8FHCfP8k4YLEWxB7UcJzgnOI54XtEHP+ZQnzd6sSXhLxDTHXfS3hFVwbEl4V8Q/xGq4tCXOubQk/M6fPzSzMOC8wfcZzczdt3qd9E6Z9B6bN4LRZfvQ78A89OOVg', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': 'bca29d8e668cef75b9fa10687b2f4b3eb1535e5c4b11619bd55255a24e503011', 'fingerprint': '7380e2b3c5060063dc9e0e581a34b31cb09bc6d3eed718d2f0ead48f2a39b1d3', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFU8mrlzz7lnzlzXO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZDKoTNmA39flwEIU52CCQLnFdWADCuvAcuCea3CwDbDXobDU5gzBf3PIZVjckZn/qsXdEHt2IkPmexaLIVMYQT4cAqcHi5DdYKxBBhiQWAT5cAh8xmwCwzmYN2SoWW/gET5Go4thND48pi6cjE/Gvel5OB7OR7I/d+fTs7BP7nttMl8ewS7u6hKjwlZaD1D4FOwTcYRMtye3oShw4QbAJeIIGbsVk8gQpw455zCv1/0cTshhh22v9v7nrx9//3x7o88as3KEQ39NQ+SylEVkFZHOYkKf13UE5z9adOnlrmMUb36KuSfJm+UY5KpJcdQjicAtsRxpsUy/JWZRcnyMKZA1cRRCz1GPRFJVWI60WKVXmVnMc3wIjilk/X4SoMYi02KdfivMokQGoDrByrJ44fjJjPMIMQ8wuI/gspA8ZXJn6bh7iMnIrfum6Q2uu5C6g9EXSdpv0KvKLXrCwuB3MHdgqibIVZPiqKdBp7TJcqTFhrAw+C1MgayJY/zoONa0QCTVFsuRFreFhcElx31wTCHXmRrdHRaZFneFhcEBqE5QGvx2MuPcRMweBjcQrAyuaCQG39WWsqHlQzrFKTm/uXtsKSsiogJ1V1PIHpd8fx1yXDvX2fLXQPZQKeJ2MiPVFFsY+AhWIioaiYgNbSmbHNvYR5zZ3JiBUUTpB32rbTD73YN1yLE+RhHlHUC3VyLi6BxpQGkj9NwVERWNRERcH5UQDVqZAo4WOLCVhEq0JCG8rhLiG6D8LS8NznslodIiSQjKzRIS3kXEDiLloaMPqcaOfE0X/83ASE35/fsHBFke6g==', 'glb': 'eNqdVltv2jAUNvROu63dum5PU+WnPaQoMSEBXkbHNqlTC1Vb9aXiwSEOiwYJSkIvqpD6z/a6n7Xj4IAJ0MJqGZ/PPuc7F/tEbXeufmQRQp9zCH1bQ+jnZaP+iGkYsghXHvEtC0LX93AFk7yKFdxmHgto5Aew06I2QDxQMLuPAhpyfbEHUo8GwIBLtmUbJZsUbatkODBs3VSpVVSpYZaLhqYSQ9cs0yKORc2WYWkmKYJsFYjTclrEPro1wK3l9z07dtB1gf1GzauKmE0Fd+k97BUAQ5AKrE2IyaEtVvM7fgBmN6DEILBahyfGN7DnewyIHUajfsCliHoQeQRSyGgXFpuJZFkSAA0eQATLM+q5jt+xAfW9355/5+Hm0GMco9V3HBZcu+wOVwoKboEtlMIYDIOYUtFHKhoROue+60VTisWRItEHA1ANWxAhrqhC4ok9Qnx2LEEVmsoIQeVY+CvWDfyIRvGl3hypeVM1NdUwS5pWMoq6aSjj2qaPmtwP7bDhBaiaIv3GzrgL4a0XuF03cm8FpFEUuFY/GmZ/3rg8uTpp1ONw6o2Ls+NTSB4Scj3bjYtIgAwih+IMYmbagu1weJmTRVF5Ubo9uE4vunrogUlRI4ZUKbjaeBtff68V4MYWfUGTbrTl3KTNySzz8dsoGCPzy9rx6fEF5kmPCeS045Sth4g1HCfuUgFPmdeO4IZJqcRfc9DmZwW9bJBxMFO2sfJ/WxfhuUxYm2TSuPCMsaGnXJeBzKNdNvq05Icd9QyHqeuTHFqZpEmGPfcMSblgzKjBNIloyvHNiFuR3RPCH+zh4eHfVYS+ntTR7L9qNREmZfmsKp8l+9VJWT6f4E3xyHsyj+xXjmce/1ScM3xM5ZXKJc3/9Afkp+FcSP4y5l1EXpg3kdGSPp6W9IGW8ZFBK7CuoAzKonW0gVZhrIG0hbbRJswtlEOv0Ru0A+MVSHvoHdqFuYfeogP0Ae3DeA9SRjCuizUr5ARzLzsShucLDGMM/xqgjxLm6ycJZyTejFizEl4RnAleFbYJ5vybEuZnOQlviPgSzHVfS3gL5q6EcyL+BG/D3Jcw5zqQ8At9+kLPznrzM/sFze/xdF/M6/d534R5cc/rwXm9PPs78A8kJey7', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'cbccb6aabd344d5ca5314286fa349e93ba29d7162354f7b55790f62395ac9f9d', 'fingerprint': '17d80fdac01c6649a482e1fa904419355b340c4a4e439cc79091905ed40b9f2c', 'payload': 'eJylVs1u00AQXju/ddPWtED5x3CCiAOkEUdwlSJxQCpSIu5W2oZKURwlBKmceA5eghdAKm/Ba1Q8AePEM5vYM9l1sap4tTM73zcz347rHY9PR0HnsNs5PHoX9OJxPIwHF8Gn9ovgWf95sLBG0350cup9iPvRl/N4NA1eep3Z5Otp62QatNqeo9jn6i2ubB3qoeB4KUTQkbIOv6/LQYQozsEE4YeCg7EOvuRQuA4iB+m5BgdbB/s6FC61OUL43xxyEeZ3ZBq8akk3xJ6dypD5nsUSyBRGoEdCkNouImQPGHOgAwYkEYEeCUGOmA1g6IP5QIaa9QEZ4WM8vBjEo4MjmMLp+njUm5xHo8FsSPO5O5ucRX1Q32uT+PIIdn5Xl7gqLKX1AIW7YB9IImS6PbkDRYELDwApkETIOJSFQAY/3eScwrxe93M0BoUdtL3a+5+/fvz98+0NHzVplKMc+GsaPBepzD2r6OnMN/i4rqMk/YHRhZe7jlFy+CnuPUnfIscwl80KRx5JhW5J5AjGMvyWBCNxfIxbSNbEUSmeI48EpaqIHMFYhVdZMOY5PkSOK8j8eShATUQGYx1+K4KRkBFQd7CySF45QbrjPEKfB7i4j85lRTwpuLNQ3D30yZSb+6bxAucuJNcYPkmo/Qa8qpLRUxYCv4N7+6Zswlw2Kxx5GtClTZEjGBvKQuC3cAvJmjgmD8exxgJBqbZEjmDcVhYCJ457yHEFuS7k6O6IyGD0lYXAEVB3kAR+O91xbqLPLi5uoLMWuKaRCtxnU9lg+fihm4Tk9QbGXTEVX6eCGei7uoLsScH31iEnufMSW0Kma0AzlIq4ne5QNdUWLgJ01kXUNNIiNthUNiW2iY54sYExYWAsIumBH7UN4by7vw45qY+xiHQHcNrrImLrHBIgyQhn7lIRNY20iHh9dEAc0FoUqGiFDVsKqIuWBkSt64D4DdD6pkuD/V4KqGuRBkTKzRIGvIseO+hJTcc5pAc7xmu6+N8MCqlJ379/iYwfsg==', 'glb': 'eNqdVktP20AQnvB+tYWW0p4qtKceDFo/YsdcGkpbiQoIAsQF5bCON9RqYiPH4SEUiX/Wa39WZ511so4THo217HzrmW9eOxaXrbMfUwDweQng2yzAz9Pa0T1hnQ5PyM49ueZxJ4hCskOMbUo0cslDHrMkivGkwXyEpKcRfpvErCP05RlKVyxGBuI2fKfi2yblXsXxXKdsNSuW61HGXepSm7oNyzdc3zB00/ZsvWIZtOFQSvUy8yzqlbeubXTrRd3QTx20A2S/oNtUk6uukTa7xTMLMQapmXiGMTVZg+9FrShGswtU4hjYXkskJg5IGIUciZucJd1YSAkLMfIEpQ5nbdx8LpPlWQAsvkMRLQ9ZGDSjlo+oG/4Oo5uQ1Pse0xi9brPJ4/OA35AdUyMNtMVS2L1+EAUVa6CiG1LnOArCpKBYHigaVq+Hqp0GRkh2qJREYvcYn59KWIW6NkBYOd75lerGUcKStKkXW3TboY5Obaei6xW7bDm2Nqzt6Ku68MNavN8AqmvK39SZcCG9XcVBO0iCawlZksSB10362R/XTvfP9mtHaThHtZPD3QNMHhMKQj9Ii2ggGUaOxemlzKyBx51+M/NFoaIo7StsZ5ic3V2hSVk3bKVS2Nr0mJx/3zOxY8+9QXk3+svcjJob48yHd8O0B+ane7sHuydEJD0kUNNOU/buEl5rNtMplfCAh5cJdtioVMRtji/FO9NybWMYTME2Vf5v6zJel5y1Y+SNzUeMbWvEtYtkIWvzwadluz9Rj3A4lpXn0F1jlKQ/c4+QuKY9pgZFEjmUw87IrqjuDUNc2M3Nzb8zAF/3j2D8r1rNhLwsfg/VoU4mD86reblazfMW9CGvM+BU7UZkVWeUvxBntei3kNdILqP8D38EQX89S/6i5PgM+dm8mQwv9PHwQh/wEh8lmMZ9GkowBXMwDzP4zKK0CMuwgGsRluA1vIEVfF6htAbvYBXXGryFDfgA6/i8R6kkGefkPiXlDAsvKwrG64sMQ4z/GsBHBYv9k4JLCm9J7lMKnpacGZ6RthkW/AsKFu+WFDwv48uw0H2t4EVcqwpekvFneBnXuoIF14aCn5jTJ2Z23J0fOy8wecYLczFh3id9EyZ9BybN4KRZHv8d+Afk9PEN', 'selectors': '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'}]


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
    tokenize: bool = True,
) -> str:
    """Seed geometry and, unless ``parts`` is False, each component's part as a build
    derives it under FIXTURE_PRODUCER (its display mesh, selector table and
    record), without native imports.

    Caller aliases are remapped to genuine geometry CIDs. Explicit malformed
    structure/geometry overrides stay malformed so completeness checks can reject them;
    ``tokenize=False`` keeps a caller's provisional ids too (a malformed tree).
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
        entry = {name: copy.deepcopy(fixture[name])
                 for name in ("kind", "codec", "brep", "faceColors", "contentHash", "fingerprint")}
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
    # The ids above are provisional: the tree names its nodes by tokens minted
    # from its structure, as a build does. Callers read them off the tree
    # (tests.python.support.tree_ids).
    if tokenize and isinstance(tree["assembly"].get("root"), dict) and "id" in tree["assembly"]["root"]:
        from cadgen._internal.tree_tokens import assign_tokens, retoken
        tree = retoken(tree, assign_tokens(tree["assembly"]["root"], occurrences, [], label=tree["label"]))
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
