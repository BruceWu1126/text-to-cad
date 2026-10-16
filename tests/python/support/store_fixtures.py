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
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': 'df0a0992298bdf82f83ba62f12a0286b49b4736399da4818bce4a41ab2a1eaef', 'payload': 'eJylVttu00AQXTvXuumFFih3DE8Q8QBpxCN1lSLxgFSkRLxbaRsqRXGUEKTyxHfwE/wAUvsX/EbEFzBOdmYTeyZrB6uKVzuzc87MnB3XOx2eD/zWcbt1fPLe70TDqB/1rvzPzVf+i+5Lf24Nx93w7Nz7GHXDr5fRYOy/9lqT0bfzxtnYbzQ9R7HP9AhXWR2qgeB4LUQwkZION+tyECHyc7BBoCF/HcwJG4ebdTlIzxocsjpkr0PuUtsjBP/NIRVhdkfG/puGdEOys1MJMj+SWAKZ3Aj0SAhS20WE5AFrDrSBjnkR6JEQ5IjJAJY+2A8EeRECG8KnqH/ViwaHJzCF9fp00BldhoPepE/zuT0ZXYRdUN9bm/jSCNn8pte4yi2l1QC5u5A9kETIdntSB/IC5x4AUiCJkHUoC4EsfqbJKYV5nfaXcAgKO2x6lQ+/fv/8++f7Oz5q3ChHOfBXt3jOU5l5ltHTmW3wcV1HSfoDowsvdxWj+PBz3Hum3yLHIJXNEkceSQVuQeQIxiL8FgQjcXyKW0jWxlEpniOPBKUqiRzBWIZXUTCmOT5GjkvI/HkoQEVEBmMVfkuCkZAR0HSwNE9eOb7ecZ6gzyNcPETnoiKeFNyZK+4B+iTKzX3TeIFzF5JrDJ8k1H4DXmXJ6KkMAr+Hewe2bIJUNksceRrQpU2RIxhrKoPA7+AWkrVxjB+OY4UFglJtiRzBuK0yCJw47iPHJeSqkKO7IyKDcVdlEDgCmg6SwO/qHec2+uzh4hY6G4EbGlrgu2wqGyyf6ZEbh+T1BsY9MZW4eToVzMDc1SVkTwq+vwo5zp2X2AIyXQOaoVTEbb1D1VRbuPDR2RTR0NBFrLGpbEpsYx3xYgNjzMBaRNIDP2prwnn3YBVyXB9rEekO4LQ3RcTWOSRAkhHO3IUiGhq6iHh9TEAc0EYUqGiFDVsIaIqmA6LWTUD8Bhh906XBfi8ENLXQAZFyvYAB76PHDnpS03EOmcGO8eou/jeDQqrT9+8fa241eQ==', 'glb': 'eNq1Vktv2kAQXvJ+tU3bNO2pivbUgxP5hW24lJS2UqokREmUS5TDYq+pVbCRWfJQhMQ/67U/q7P2GhbzCI1UJO/ON575ZnZmB2g0L78vIIQ+bSD0dRmhHxe100dMOh3KcPkR39K4E0QhLmP9QMUKbtCQxoRFMWhc4gHEPQXTexaTDrcXOpDaJGbcjdYtU/V9x6XEcQ3Pck3Vsg3dJCYxDOo5RdvwPL/uGiVbc+vULDpF1XIc4nuu4/j2/q0NYetRN/SSAK0A2K/VA1URz42CW+QedBpgSFIxQAc5+cSl1agZxeB2DUYUEqs2+cG4AodRSIHYp4R1Yy4xEkLmDKQOJS3YPCoOS7MESPwAIniekDDwo6YHqBv+CqO7EN+kEZMc613fp/FVQO9w2VCwC75QCquXJjFmYg5MNF3YnEVByMYMiwND3ez1wLTjQoa4rAqJH+wR8vMSCapwowwQVI52fia2ccQIS5p6va8e2KqtQUccTXOsomlbyrC2+Vc3PA5p0rQBqqZIaxKMhxDR2nHQClhwKyBhLA7qXZae/qx2cXR5VDtN0jmtnZ8cHsPh4UBB6AVJEXUgg8yhOL2Embig7qTNHC2KyovSakM7Q3b50AaXoqZbUqWgtYkaX32rGtCxeW/QaBjt38Lk3fVJ7sO7YVgD94vq4fHhOeaHHhLIx06OXH9gtOb7yZQKeEzDBoMO647Db3Pc4O8Ms2Tpw2TGfBPjZ3sX4bqMeNv6qLMxw9kyc6FLQBaSFh18tRykEzWDwzbNUQ6tpOdJ0pmbQVIyrAk1GCcRQznsjOiKHF7X+YXd29v7s4TQl6NTNPlTqWSCrEz1/c9DVSYP9BVJX5F4cvb5WDKPHFfmHMthAv+YL5rCk4uVz0Hm7//mBIJnHjmL259Pnps3k/vPiNHP+c+S0ZT6TZQLiP86F9Ai7MtoCa3AuoJW0SZaQ+uwbsD+Cr1AW7BuoZfoLdpGr2F9A/t79A7twLqDdoEh/ayIfUHIGV6EZ0vCcH2BY4jhrwH6IGG+f5RwQeItiH1BwouCM8NLwjfDnH9NwvzdhoRXRX4Z5rYvJbwOz7aEN0T+Gd6EZ0fCnGtXwk/M6YT7PG1+554X9MRcTJn3p+bu/3wP/AXmWPK7', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'd0a73c5078a3634e889f05f9614b6fdbe678ea9f6f4702e98cb6aafd05741fae', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFE8mrlzzrl3ztzEO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZBAUUjbg93U1sBTFNRgpAibAXAcuoHgdOA3ccw0NtgH2dSheaiNC8N8acgiLOzLzX7W4G2KvTmTEfM9yMWIKM8iHZTAg5xkyG8w5BJl3UQb5sAwsYhbAcA7mDRlp1ht4ho/R6GIYjQ+PqQsn45Nxb3oejofzkezP3fn0LOyT+16bzJdnsIu7usSosJXWExQ+BXsgTpDp9uQ2FCUu3AA4IE6QsSkzQIY4dcg5h3m97udwQg47bHu19z9//fj759sbPWp8UI5w6NM0RC5TWURWEeksJvS4riM4/9GiSy93naJ481PMPUnerMYgl01Ko55JBG6J1UiLZfouMYtS42NMQaxJoxB6jXomKlWF1UiLVXqVmcW8xofQmGLW76cC1FhmWqzTd4VZlMwgVCdYWSYvHD+ZcR4h5gEG9xFcFlKnBHeWjruHmGy5g7SWNQbPRC7ANQejT5Jqv0GvKrfoCQuD38HcgSmbIJdNSqNeBp3SJquRFhvCwuC3MAWxJo3xo9NY0xJRqbZYjbS4LSwMLjXuQ2OKuc7k6O6wzLS4KywMDkJ1gtLgt5MZ5yZi9jC4gWBlcCUjMfiuNpUNTm0Myba5PZtUkIG6qylmjwPfX8cc5260mLwGsofKIm4nM7KaYgsDH8GqiEpGUsSGNpVNTm3sI7Ybxgrs/aBvtQ3tdgI/WMcc18f+nqLbqyLi6BxpQGkj9NyVIioZSRFxfRQgGrQyBRwtcGArgKpoCSC8rgDxG6D8LS8NznsFUNUiAYTkZgmAdxGxg0h56OhDqrEDr+ni3wyM1JS/f/8AfxceIg==', 'glb': 'eNq1Vltv2jAUNr2Xdlu3dd2epspPe0hREnKDl9GxTerUQtVWfal4MInDokGCktCLKiT+2V73s3acGDDhUlZpSLbPZ5/znYt90rbaV99XEEKf8gh9XUfox2W99ohJFNEYlx/xLQ0jL/BxGasFGUu4RX0akjgIYccmDkDclzC9j0MSMX2+B1KXhMCANV23iGPJStOmmuJqiuXIhkMIkS1T01TqEL1Usg1FNS3ZNhzqkmaJGo5qukVb1pv60a0JbptBz3cSBx0P2G/kgizx0ZBwh9zDHgQosVGEPYjJJTatBu0gBLMbUKIQWLXNEmMb2A98CsQuJXEvZFJMfIg8BimipAOLQ3mydBgACR9ABMsz4ntu0HYA9fxffnDn40bqMYmx2XNdGl579A6XixK2wRZKYfTTIKZUtJGKonKd88Dz4ylFfaSoav0+qEY2RIjLMpdYYo8Qn5NIUIWGNEJQORr9THTDICZxcqk3R3LBlE1FNkxLUSxD10xDGtc2e9RgfkibphcgK5IwJ86YC+6tG3odL/ZuOSRxHHrNXpxmf16/PLk6qdeScGr1i7PjU0geEvJ8x0uKqAIZRA7F6SfMxIbtKL3MyaLIrCidLlynH189dMFEV1RDqBRcbbKNr79Vi3Bjy76gSTfKv7nJmquzzMdvo2iMzC+rx6fHF5glPSYQ005Sbj7EtO66SZdyeEr9Vgw3rFoWe81hi50VtZKhjoOZsk2Un22tw3OZsDbVSePiAmNDy7guAZlPOnT0aSmkHbWAAz4hkxxKSc2SpD23gKRUNGbUYJqEN+X4ZvitiO5VlT3Yw8PDP2sIfTmpodm/SmUoiJt8P7M30hfPKoI+mtaf8lWZ47eyIIZZ/FlbNIcn42sqBoF/8BvkQXq2lPyZ8w6Wk5fmHcqDZ/gYZOwXySiTx0I5h9hf5xxahXUdraENmDfQJtpBW2gb5jysr9ALtAvzLnqJ3qI99BrmN7C+R+/QPsz76AAY0t8GX1e4PMSrMHYFDM8XOMYY/jVAHwTM1o8Czgm8Ob6uCHiVcw7xGrcdYsa/JWB2lhfwJo9viJnuSwFvw9gTcJ7HP8Q7MPYFzLgOBPxEn856z/P6d9l+QYv7Ym6/P9F3/+c78BegAOaM', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': '9335cb8f0ab89f4653a59de00502faba93777fd7a5f8f75509cca4e22aedd342', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFU8mrlzz7lnzlzXO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZDKoTNmA39flwEIU52CCQLnFdWADCuvAcuCea3CwDbDXobDU5gzBf3PIZVjckZn/qsXdEHt2IkPmexaLIVMYQT4cAqcHi5DdYKxBBhiQWAT5cAh8xmwCwzmYN2SoWW/gET5Go4thND48pi6cjE/Gvel5OB7OR7I/d+fTs7BP7nttMl8ewS7u6hKjwlZaD1D4FOwTcYRMtye3oShw4QbAJeIIGbsVk8gQpw455zCv1/0cTshhh22v9v7nrx9//3x7o88as3KEQ39NQ+SylEVkFZHOYkKf13UE5z9adOnlrmMUb36KuSfJm+UY5KpJcdQjicAtsRxpsUy/JWZRcnyMKZA1cRRCz1GPRFJVWI60WKVXmVnMc3wIjilk/X4SoMYi02KdfivMokQGoDrByrJ44fjJjPMIMQ8wuI/gspA8ZXJn6bh7iMnIrfum6Q2uu5C6g9EXSdpv0KvKLXrCwuB3MHdgqibIVZPiqKdBp7TJcqTFhrAw+C1MgayJY/zoONa0QCTVFsuRFreFhcElx31wTCHXmRrdHRaZFneFhcEBqE5QGvx2MuPcRMweBjcQrAyuaCQG39WWsqHlQzrFKTm/uXtsKSsiogJ1V1PIHpd8fx1yXDvX2fLXQPZQKeJ2MiPVFFsY+AhWIioaiYgNbSmbHNvYR5zZ3JiBUUTpB32rbTD73YN1yLE+RhHlHUC3VyLi6BxpQGkj9NwVERWNRERcH5UQDVqZAo4WOLCVhEq0JCG8rhLiG6D8LS8NznslodIiSQjKzRIS3kXEDiLloaMPqcaOfE0X/83ASE35/fsHBFke6g==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIL9bApaS0lVIlIUqiXCIOa3tNrYKNjMlDERL/rNf+rM7aCyyGEBqpSN6dbzzzzWN3gGbr8vsSQuhTHqGvqwj9uKifPmDa7bIYVx7wDYu6fhjgCtYLKlZwkwUsonEYgcahLkDcVzC7iyPa5fZCB1KHRsCAbc/wNFUreVaZ2GWPlEzHJjbRNIfpLikVqVG0Nc9RNWqrtuMQ13GJRkqWWaaGatHywY0FYe2wF7hJgLYP7NdqQVXE01Bwm96BzgAMSSqwNyAnjzqsFrbCCNyuwYhBYrUWL4wrcBAGDIg9RuNexKWYBpB5DFKX0TZsLhPFsmECNLoHETxPaOB7YcsF1At+BeFtgBtpxCRHu+d5LLry2S2uGAp2wBdaQfppElMm5shE04XNWegH8ZRhcWSom/0+mHYdyBBXVCHxwh4gPzeRoAsNZYSgc6z7M7GNwpjGyaFeH6gFS7U0lVglTSuRomkRZdzb7KsGj0NbLD0AVVOkNQnGQ4honchv+7F/IyCN48i3e3Fa/Vn94ujyqH6apHNaPz85PIbioSA/cP2kiTqQQebQnH7CTB1Qd9PDnGyKypvS7sBxBvHlfQdcippOpE7B0SZqfPWtZsCJLXqDJsNo/xYm667Pch/fDYOM3C9qh8eH55gXPSaQy05Ktu9jVve8ZEoFPGZBM4YT1kslfpujJn9nmGWij5OZ8k2Mn+1dhOsy4W3pk87GHGdiZkKXgSygbTb6aimkEzWHwzLNSQ6trGdJ0pmbQ1I2yIweTJOIoRyfjDgVObyu8wu7v7//ZwWhL0enaPanWh0KsjLVV6vTdiO9hFHGdoI3o5N55Lgy51QO1dl8qDoda4onEyubg8w/+A3yIH23kPxZ8A4WkxfmHcqDZ8QYZPznyShTx1w5h/ivcw4tw76KVtAarGtoHW2hDbQJax72V+gF2oZ1G71Eb9EOeg3rG9jfo3doF9ZdtAcM6WdN7EtCHuJleLYlDNcXOMYY/hqgDxLm+0cJ5yTenNiXJLwsOId4RfgOMeffkDB/l5fwushviLntSwlvwrMj4bzIf4i34NmVMOfak/ATczrjPj82vwvPC5o/F4/N+1Nz93++B/4CniPsjg==', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'e1c7f4c4ad854cd0fbfa1e3cf5601b07cbe9f7185db2010ca41024fb6b7ba9b8', 'payload': 'eJylVs1u00AQXju/ddPWtED5x3CCiAOkEUdwlSJxQCpSIu5W2oZKURwlBKmceA5eghdAKm/Ba1Q8AePEM5vYM9l1sap4tTM73zcz347rHY9PR0HnsNs5PHoX9OJxPIwHF8Gn9ovgWf95sLBG0350cup9iPvRl/N4NA1eep3Z5Otp62QatNqeo9jn6i2ubB3qoeB4KUTQkbIOv6/LQYQozsEE4YeCg7EOvuRQuA4iB+m5BgdbB/s6FC61OUL43xxyEeZ3ZBq8akk3xJ6dypD5nsUSyBRGoEdCkNouImQPGHOgAwYkEYEeCUGOmA1g6IP5QIaa9QEZ4WM8vBjEo4MjmMLp+njUm5xHo8FsSPO5O5ucRX1Q32uT+PIIdn5Xl7gqLKX1AIW7YB9IImS6PbkDRYELDwApkETIOJSFQAY/3eScwrxe93M0BoUdtL3a+5+/fvz98+0NHzVplKMc+GsaPBepzD2r6OnMN/i4rqMk/YHRhZe7jlFy+CnuPUnfIscwl80KRx5JhW5J5AjGMvyWBCNxfIxbSNbEUSmeI48EpaqIHMFYhVdZMOY5PkSOK8j8eShATUQGYx1+K4KRkBFQd7CySF45QbrjPEKfB7i4j85lRTwpuLNQ3D30yZSb+6bxAucuJNcYPkmo/Qa8qpLRUxYCv4N7+6Zswlw2Kxx5GtClTZEjGBvKQuC3cAvJmjgmD8exxgJBqbZEjmDcVhYCJ457yHEFuS7k6O6IyGD0lYXAEVB3kAR+O91xbqLPLi5uoLMWuKaRCtxnU9lg+fihm4Tk9QbGXTEVX6eCGei7uoLsScH31iEnufMSW0Kma0AzlIq4ne5QNdUWLgJ01kXUNNIiNthUNiW2iY54sYExYWAsIumBH7UN4by7vw45qY+xiHQHcNrrImLrHBIgyQhn7lIRNY20iHh9dEAc0FoUqGiFDVsKqIuWBkSt64D4DdD6pkuD/V4KqGuRBkTKzRIGvIseO+hJTcc5pAc7xmu6+N8MCqlJ379/iYwfsg==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIb2MuJaWtlCoJURLlEnFYzJpaBRvZJg9FSPyzXvuzOmuvYTGE0EhF8u5845lvHrsDtDuX35cQQp+KCH1dRejHRf30AZMoojGuPOAbGkZe4OMKVksylnCb+jQkcRCCxiEtgHggYXoXhyRi9lwHUo+EwIAVR7UpUQ2rqZlOS7csm5qGpZV1Wy/rZdnVbMVoGoaqlW2DGi51dEN2ZYc0Hc0gmmod3FgQthn0/VYSoOsB+7VckiX+NCTcJXeg0wFDkpIGOsjJJQ6tBZ0gBLdrMKKQWK3DCmMK7Ac+BWKXkrgfMikmPmQegxRR0oWtRXmxNEuAhPcggucJ8T036LQA9f1ffnDr40YaMcmx2XddGl559BZXNAk74AutMAdpElMm+shEUbnNWeD58ZShMTJU9cEATCMHMsQVmUussAfIr5VI0IWGNELQORr9TGzDICZxcqjXB3LJki1FNq2yopRNQ7dMadzb/KsGi0M6ND0AWZGENQnGQvBovdDrerF3wyGJ49Br9uO0+rP6xdHlUf00See0fn5yeAzFQ0Ge3/KSJqpABplDcwYJM3FAHaWHOdkUmTWl24Pj9OPL+x64GIpqCp2Co03U+OpbTYMTW/QGTYZR/i1M3l2d5T6+G5o5cr+oHR4fnmNW9JhALDspuXkf07rrJlPK4TH12zGcsFous9scttk7TbdNdZzMlG9i/GxvA67LhLelTjprc5xNPRfaBjKfdOnoq6WUTtQcDkvXJzkUW82TpDM3h8TWzBk9mCbhQzk+GX4qYnhVZRd2f3//zwpCX45O0exPtZoJojLVDwVdJo/0VUFfFXhy9vlYIo8YV+ScymEG/5QveoQnFyufg8g//M0IOM8i8ucs6GLywryZPHxGjGHOf56McnXMlQuI/ToX0DLsq2gFrcG6htbRFtpAm7AWYX+FXqBtWLfRS/QW7aDXsL6B/T16h3Zh3UV7wJB+1vi+xOUML8OzLWC4vsAxxvDXAH0QMNs/Crgg8Bb4viTgZc6Z4RXum2HGvyFg9q4o4HWeX4aZ7UsBb8KzI+Aizz/DW/DsCphx7Qn4iTmdcZ8fm9+F5wU9MRePzPtTc/d/vgf+AtrU8Vs=', 'selectors': 'eNqtWMuuozgQ/RfWpOUXGLLteSxmMdJ0qzdRNDK2SZghEAHJ9FUr/z7lB4QkkNC6VxEQMK46dVwv8yNo5V4fxDfdtEVdBWsaBsemzotSB+tANF2RC9kFYaCrrnn7o6gUPD7Cc3gkxVFkRVl0hW6D9Y9Aq53+XIq2LfJCis6K+xGIclc3Rbc/wMRarrJGH1eyrrqiOhXd2+qMQZKodqdSNF/rUjeikvoXvQvWJAxacTiWRji9wM2pASz6V9Dyl66Ubopqd6/AQFiVRaVbJ/hctIWF+GaBGVGbINeiOzUahjtQDIbBv1aLA1yUhnvA0OlgCyoBZ6Vlp9UXXcLVW2Tt3IvCm1fsqgMIsZjpJ3QZxP4GaEFh15z0BZ5mWf3dzDiYiRv0CYX+2IbBQcDQhsE9gQOkGO1tJzpLbC3lqWk0EPO5PgHcNQ6DUov8z4nn7V4cR7eGMX8Xhw65HwN6z7rp9Hf/ILm48Rtu38PfA/tWkTWolwAoLpavrNQPlpanQ2X1deATbV43RoEl0Vv5pXN+ODLZGdwPXI13pvXPrzT0HPQjY0a2g+QBSd2oohIlvPiviYQBjgQSdGMcudHCiDFT9E+j2fYvDQorYcVc9VpEwRAMX9/snVc7wKiALPu6xzdnfANufTYBZ+CVYtfCFYJbHNrr4v7e1KejgeYmvoYGK3jugZW62nX7MTSPaI6Y+eV4gVaof4T0QddPuKYZ45rFXqtGlCZOH1zTvGwjentVusDSO6NmVhU8/OrXRuJmg6/RPxw/8WwbLk8jMAGO2FwJnJJt79cWickYdVmALy+XuCEeGf4UbcPI3BHzRq9l6/3YKgjytUaUxJTijNE4ADSg81iKChiMYdYGjaVtVubvagDwDBW6s5OFzFgIWaQpdhOzxPfCQOo5NUpgHgalBmQWxZHSKkOYBiG5B8luQN6IGDCyl8yxCYxsAUbiMBKOuGA5V7EEIukIIzY67LqgAaSdjx9Qzq0vGlAmgDKKFzC5GrkozKMOJTCYkIxlnGtwKzaJklzXeyzjBiR5SiU48zRK8gIlcygFVXmWZ4pjRYIwGqFMepADKidi1RO6LPq8d8VmyfkCMsfhfQkjhzKNmVSEYoFj44lzKKlF+TRB0FkmCXoCkc5DjLe+KLg412uEVISUjHKmkY9z04oFRsgYXfQ6rNEorIk9KA9jNk+iKpq+QbuDObSR6u+9aMAjUzN2bWJs/Ot1ElON8lwxlCIf/w48Geco+ora25xE/I+SGfD0Afydt75CTxx6IblKmdRIxtxnhjvqyRT15GlGZSGxxyT15AOopw48iXOaqlzFjCKfMCaoX1wOHHExaI+fUY/eSz3zjsOl1AnRGeJ9IhlRz6a8ni1IwcQeD9SzD/L6yIHXmBAlMU8py3x+GVHPpryevUwo2MSrPT2Qzz7K72OHP8Kcap1KpFUahHyCfDJF/svSQtxpkv6P8Hzu4MsoynCcKZYiBil9hv7FTYYvOSZm7WmW/nf7fuLwqyxNYp7HUsgc3rniZ+N2ZHH7MZRM4k4ML47d+4bpBf60T5sog2olsEgAP0YzBtAlBfUavNhErj3NGkDfawBGzoI0JkksMgZtgumt8YQF5GeaK9S3BMSdJi0gH7EE2BdehXiEscgTqahpEWcsoFMWPGlqbOElzyx4/xrYnY7ZLhb9Zue8VgIlCGuWEZq4Juh20V81D+POx9bH8xphqbM84pwn2LUmt4GwpJnyHXovklJNoU/jMtGp6xduaXlG9G2TEPciGZEsZxjJSESuit/6ypK+w6FMB8MlwwIxKlCOXGm9LSCvShIap3QvU0WM84jpPO0r3m1WXFKjfZ6KBjaximE3wbVN47yXSaZwPndbyBq9zBR2J7kUJM20Kw23xWdJOfNujEHm1n4+sR9l3Re4/ktq/Z/t7EE+UATLBhUPmDZbwNRs5m0px2aVzX9ugaRmKbBv/s33lkGIadUj47h2N46tr5plMw7Wd5ORVUNNB2IFfLOfW7wI86nCNg52Krxkt8yxR8WtOGSnc9PfDV9r7kxJLYbEIXCWYTMtghFmrYjM0mMrGVLp5fI/SbJB3A=='}]


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
    from cadgen.store.results import producer_versions
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
                 for name in ("kind", "codec", "brep", "faceColors", "contentHash")}
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
              "outputs": {str(document.resolve()): {"sha256": sha}}, "producer": producer_versions()}
    if sidecar:
        record.update(sidecar)
    write_record(owner, record)
    if sha:
        note_document_tree(sha, tree_hash, kind="step", producer=FIXTURE_PRODUCER)
    if model is not None:
        note_output(document, owner)
    return tree_hash
