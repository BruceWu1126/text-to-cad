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
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': 'df0a0992298bdf82f83ba62f12a0286b49b4736399da4818bce4a41ab2a1eaef', 'payload': 'eJylVttu00AQXTvXuumFFih3DE8Q8QBpxCN1lSLxgFSkRLxbaRsqRXGUEKTyxHfwE/wAUvsX/EbEFzBOdmYTeyZrB6uKVzuzc87MnB3XOx2eD/zWcbt1fPLe70TDqB/1rvzPzVf+i+5Lf24Nx93w7Nz7GHXDr5fRYOy/9lqT0bfzxtnYbzQ9R7HP9AhXWR2qgeB4LUQwkZION+tyECHyc7BBoCF/HcwJG4ebdTlIzxocsjpkr0PuUtsjBP/NIRVhdkfG/puGdEOys1MJMj+SWAKZ3Aj0SAhS20WE5AFrDrSBjnkR6JEQ5IjJAJY+2A8EeRECG8KnqH/ViwaHJzCF9fp00BldhoPepE/zuT0ZXYRdUN9bm/jSCNn8pte4yi2l1QC5u5A9kETIdntSB/IC5x4AUiCJkHUoC4EsfqbJKYV5nfaXcAgKO2x6lQ+/fv/8++f7Oz5q3ChHOfBXt3jOU5l5ltHTmW3wcV1HSfoDowsvdxWj+PBz3Hum3yLHIJXNEkceSQVuQeQIxiL8FgQjcXyKW0jWxlEpniOPBKUqiRzBWIZXUTCmOT5GjkvI/HkoQEVEBmMVfkuCkZAR0HSwNE9eOb7ecZ6gzyNcPETnoiKeFNyZK+4B+iTKzX3TeIFzF5JrDJ8k1H4DXmXJ6KkMAr+Hewe2bIJUNksceRrQpU2RIxhrKoPA7+AWkrVxjB+OY4UFglJtiRzBuK0yCJw47iPHJeSqkKO7IyKDcVdlEDgCmg6SwO/qHec2+uzh4hY6G4EbGlrgu2wqGyyf6ZEbh+T1BsY9MZW4eToVzMDc1SVkTwq+vwo5zp2X2AIyXQOaoVTEbb1D1VRbuPDR2RTR0NBFrLGpbEpsYx3xYgNjzMBaRNIDP2prwnn3YBVyXB9rEekO4LQ3RcTWOSRAkhHO3IUiGhq6iHh9TEAc0EYUqGiFDVsIaIqmA6LWTUD8Bhh906XBfi8ENLXQAZFyvYAB76PHDnpS03EOmcGO8eou/jeDQqrT9+8fa241eQ==', 'glb': 'eNq1Vktv2kAQXvJ+tU3bNO2pivbUgxP5hW24lJS2UqokREmUS5TDYq+pVbCRWfJQhMQ/67U/q7P2GhbzCI1UJO/ON575ZnZmB2g0L78vIIQ+bSD0dRmhHxe100dMOh3KcPkR39K4E0QhLmP9QMUKbtCQxoRFMWhc4gHEPQXTexaTDrcXOpDaJGbcjdYtU/V9x6XEcQ3Pck3Vsg3dJCYxDOo5RdvwPL/uGiVbc+vULDpF1XIc4nuu4/j2/m0JwtajbuglAVoBsF+rB6oinhsFt8g96DTAkKRigA5y8olLq1EzisHtGowoJFZt8oNxBQ6jkAKxTwnrxlxiJITMGUgdSlqweVQclmYJkPgBRPA8IWHgR00PUDf8FUZ3Ib5JIyY51ru+T+OrgN7hsqFgF3yhFFYvTWLMxByYaLqwOYuCkI0ZFgeGutnrgWnHhQxxWRUSP9gj5OclElThRhkgqBzt/Exs44gRljT1el89sFVbg444muZYRdO2lGFt869ueBzSpGkDVE2R1iQYDyGiteOgFbDgVkDCWBzUuyw9/Vnt4ujyqHaapHNaOz85PIbDw4GC0AuSIupABplDcXoJM3FB3UmbOVoUlRel1YZ2huzyoQ0uRU23pEpBaxM1vvpWNaBj896g0TDav4XJu+uT3Id3w7AG7hfVw+PDc8wPPSSQj50cuf7AaM33kykV8JiGDQYd1h2H3+a4wd8ZZsnSh8mM+SbGz/YuwnUZ8bb1UWdjhrNl5kKXgCwkLTr4ajlIJ2oGh22aoxxaSc+TpDM3g6RkWBNqME4ihnLYGdEVObyu8wu7t7f3ZwmhL0enaPKnUskEWZnq+5+Hqkwe6CuSviLx5OzzsWQeOa7MOZbDBP4xXzSFJxcrn4PM3//NCQTPPHIWtz+fPDdvJvefEaOf858loyn1mygXEP91LqBF2JfRElqBdQWtok20htZh3YD9FXqBtmDdQi/RW7SNXsP6Bvb36B3agXUH7QJD+lkR+4KQM7wIz5aE4foCxxDDXwP0QcJ8/yjhgsRbEPuChBcFZ4aXhG+GOf+ahPm7DQmvivwyzG1fSngdnm0Jb4j8M7wJz46EOdeuhJ+Y0wn3edr8zj0v6Im5mDLvT83d//ke+Av5ZPK9', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'd0a73c5078a3634e889f05f9614b6fdbe678ea9f6f4702e98cb6aafd05741fae', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFE8mrlzzrl3ztzEO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZBAUUjbg93U1sBTFNRgpAibAXAcuoHgdOA3ccw0NtgH2dSheaiNC8N8acgiLOzLzX7W4G2KvTmTEfM9yMWIKM8iHZTAg5xkyG8w5BJl3UQb5sAwsYhbAcA7mDRlp1ht4ho/R6GIYjQ+PqQsn45Nxb3oejofzkezP3fn0LOyT+16bzJdnsIu7usSosJXWExQ+BXsgTpDp9uQ2FCUu3AA4IE6QsSkzQIY4dcg5h3m97udwQg47bHu19z9//fj759sbPWp8UI5w6NM0RC5TWURWEeksJvS4riM4/9GiSy93naJ481PMPUnerMYgl01Ko55JBG6J1UiLZfouMYtS42NMQaxJoxB6jXomKlWF1UiLVXqVmcW8xofQmGLW76cC1FhmWqzTd4VZlMwgVCdYWSYvHD+ZcR4h5gEG9xFcFlKnBHeWjruHmGy5g7SWNQbPRC7ANQejT5Jqv0GvKrfoCQuD38HcgSmbIJdNSqNeBp3SJquRFhvCwuC3MAWxJo3xo9NY0xJRqbZYjbS4LSwMLjXuQ2OKuc7k6O6wzLS4KywMDkJ1gtLgt5MZ5yZi9jC4gWBlcCUjMfiuNpUNTm0Myba5PZtUkIG6qylmjwPfX8cc5260mLwGsofKIm4nM7KaYgsDH8GqiEpGUsSGNpVNTm3sI7Ybxgrs/aBvtQ3tdgI/WMcc18f+nqLbqyLi6BxpQGkj9NyVIioZSRFxfRQgGrQyBRwtcGArgKpoCSC8rgDxG6D8LS8NznsFUNUiAYTkZgmAdxGxg0h56OhDqrEDr+ni3wyM1JS/f/8AfxceIg==', 'glb': 'eNq1Vltv2jAUNr2Xdlu3dd2epspPe0hREpKQ8DI6tkmdWqjaqi8VDyZxWDRIUAi9qELin+11P2vHiQMmXMoqDcn2+exzvnOxT9pW++r7CkLoUx6hr+sI/bis1x4x6fVohMuP+JaGPS/wcRmrBRlLuEV9GpIoCGHHJg5APJAwvY9C0mP6fA+kLgmBAWu6bhLHlJWmTTXF1RTTkQ2HECKbJU1TqUN0y7INRS2Zsm041CVNixqOWnKLtqw39aNbC9w2g77vxA46HrDfyAVZ4qMh4Q65hz0IUGKjCHsQk0tsWg3aQQhmN6BEIbBqmyXGNrAf+BSIXUqifsikiPgQeQRSj5IOLA7lydI0ABI+gAiWZ8T33KDtAOr7v/zgzseNxGMcY7PvujS89ugdLhclbIMtlMIYJEFMqWgjFUXlOueB50dTivpIUdUGA1Dt2RAhLstcYok9QnxOLEEVGtIIQeVo72esGwYRieJLvTmSCyW5pMhGyVQU09C1kiGNa5s9ajA/pE2TC5AVSZhjZ8wF99YNvY4XebcckigKvWY/SrI/r1+eXJ3Ua3E4tfrF2fEpJA8Jeb7jxUVUgQwih+IMYmZiw3YvuczJosisKJ0uXKcfXT10wURXVEOoFFxtvI2vv1WLcGPLvqBJN8q/ucmaq7PMx2+jaIzML6vHp8cXmCU9JhDTjlNuPkS07rpxl3J4Sv1WBDesmiZ7zWGLnRU1y1DHwUzZxsrPttbhuUxYl9RJ4+ICY0PLuLaAzCcdOvq0FJKOWsABn5BJDsVSsyRJzy0gsYrGjBpMk/CmHN8MvxXRvaqyB3t4ePhnDaEvJzU0+1eppIK4yfczeyN98awi6KNp/SlflTl+KwtimMWftUVzeDK+pmIQ+Ie/QR4mZ0vJnznvcDl5ad5UHj7DxzBjv0hGmTwWyjnE/jrn0Cqs62gNbcC8gTbRDtpC2zDnYX2FXqBdmHfRS/QW7aHXML+B9T16h/Zh3kcHwJD8Nvi6wuUUr8LYFTA8X+AYY/jXAH0QMFs/Cjgn8Ob4uiLgVc6Z4jVum2LGvyVgdpYX8CaPL8VM96WAt2HsCTjP40/xDox9ATOuAwE/0aez3vO8/l22X9Divpjb70/03f/5DvwFswzmjg==', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': '9335cb8f0ab89f4653a59de00502faba93777fd7a5f8f75509cca4e22aedd342', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFU8mrlzz7lnzlzXO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZDKoTNmA39flwEIU52CCQLnFdWADCuvAcuCea3CwDbDXobDU5gzBf3PIZVjckZn/qsXdEHt2IkPmexaLIVMYQT4cAqcHi5DdYKxBBhiQWAT5cAh8xmwCwzmYN2SoWW/gET5Go4thND48pi6cjE/Gvel5OB7OR7I/d+fTs7BP7nttMl8ewS7u6hKjwlZaD1D4FOwTcYRMtye3oShw4QbAJeIIGbsVk8gQpw455zCv1/0cTshhh22v9v7nrx9//3x7o88as3KEQ39NQ+SylEVkFZHOYkKf13UE5z9adOnlrmMUb36KuSfJm+UY5KpJcdQjicAtsRxpsUy/JWZRcnyMKZA1cRRCz1GPRFJVWI60WKVXmVnMc3wIjilk/X4SoMYi02KdfivMokQGoDrByrJ44fjJjPMIMQ8wuI/gspA8ZXJn6bh7iMnIrfum6Q2uu5C6g9EXSdpv0KvKLXrCwuB3MHdgqibIVZPiqKdBp7TJcqTFhrAw+C1MgayJY/zoONa0QCTVFsuRFreFhcElx31wTCHXmRrdHRaZFneFhcEBqE5QGvx2MuPcRMweBjcQrAyuaCQG39WWsqHlQzrFKTm/uXtsKSsiogJ1V1PIHpd8fx1yXDvX2fLXQPZQKeJ2MiPVFFsY+AhWIioaiYgNbSmbHNvYR5zZ3JiBUUTpB32rbTD73YN1yLE+RhHlHUC3VyLi6BxpQGkj9NwVERWNRERcH5UQDVqZAo4WOLCVhEq0JCG8rhLiG6D8LS8NznslodIiSQjKzRIS3kXEDiLloaMPqcaOfE0X/83ASE35/fsHBFke6g==', 'glb': 'eNq1VktP20AQXt4QaEtbSnuq0J56MJFfWcdcGpq2EhUQBIgLymFtr6nVxEaOw0MoUv5Zr/1ZnbU3ycYOIUVqJO/ON5755rE7Sa5a59/nEUKfSgh9XULox1nj+AHTTocleO8B37C4E0Qh3sN6WcUKvmIhi2kSxaBxqQcQ9xTM7pKYdri90IF0TWNgwI5v+JqqVX3LJo7tk6rpOsQhmuYy3SPVCjUqjua7qkYd1XFd4rke0UjVMm1qqBa1d29sCOtE3dBLA7QDYL9Uy6oinqaC2/QOdAZgSFKBvQk5+dRl9agVxeB2CUYMEqu3eGFcgcMoZEDsM5p0Yy4lNITME5A6jLZh85golg0SoPE9iOB5RMPAj1oeoG74K4xuQ9zMIqY5Ol3fZ/FFwG7xnqFgF3yhFaSXJVEwMYcmmi5sTqIgTAqGlaGhbvZ6YNpxIUO8pwqJF/YA+XmpBF1oKkMEnWOdn6ltHCU0SQ/1clctW6qlqcSqalqVVEyLKKPe5l81eRzaYtkBqJoirWkwHkJEu46DdpAENwLSJIkDp5tk1Z80zg7ODxrHaTrHjdOj/UMoHgoKQi9Im6gDGWQOzemlzNQFdSc7zPGmqLwp7Ws4zjA5v78Gl4qmE6lTcLSpGl98qxtwYrPeoPEw2r+Fybvrk9xHd8MgQ/ez+v7h/inmRY8I5LLTkp37hDV8P51SAQ9ZeJXACevVKr/N8RV/Z5g20UfJFHxT42d7V+C6jHlb+rizMcWZmLnQNpCFtM2GXy3lbKKmcFimOc6h2XqeJJu5KSS2QSb0oEgihnJ0MuJU5PC6zi/szs7On0WEvhwco8mfWm0gyMpMX6sV7YZ6CaOc7RhvTifzyHFlzkIOtcl8qFaMVeDJxcrnIPP3f4Pcz97NJH8WvP3Z5Jl5B3L/GTH6Of9pMsrVMVWeQ/zXeQ4twL6EFtEyrMtoBa2jVbQGawn2V+gF2oB1A71Eb9Emeg3rG9jfo3doC9YttA0M2WdZ7PNCHuAFeDYkDNcXOEYY/hqgDxLm+0cJz0m8c2Kfl/CC4BzgReE7wJx/VcL8XUnCKyK/Aea2LyW8Bs+mhEsi/wFeh2dLwpxrW8JPzOmE+/zY/M48L2j6XDw270/N3f/5HvgLsS/skA==', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'e1c7f4c4ad854cd0fbfa1e3cf5601b07cbe9f7185db2010ca41024fb6b7ba9b8', 'payload': 'eJylVs1u00AQXju/ddPWtED5x3CCiAOkEUdwlSJxQCpSIu5W2oZKURwlBKmceA5eghdAKm/Ba1Q8AePEM5vYM9l1sap4tTM73zcz347rHY9PR0HnsNs5PHoX9OJxPIwHF8Gn9ovgWf95sLBG0350cup9iPvRl/N4NA1eep3Z5Otp62QatNqeo9jn6i2ubB3qoeB4KUTQkbIOv6/LQYQozsEE4YeCg7EOvuRQuA4iB+m5BgdbB/s6FC61OUL43xxyEeZ3ZBq8akk3xJ6dypD5nsUSyBRGoEdCkNouImQPGHOgAwYkEYEeCUGOmA1g6IP5QIaa9QEZ4WM8vBjEo4MjmMLp+njUm5xHo8FsSPO5O5ucRX1Q32uT+PIIdn5Xl7gqLKX1AIW7YB9IImS6PbkDRYELDwApkETIOJSFQAY/3eScwrxe93M0BoUdtL3a+5+/fvz98+0NHzVplKMc+GsaPBepzD2r6OnMN/i4rqMk/YHRhZe7jlFy+CnuPUnfIscwl80KRx5JhW5J5AjGMvyWBCNxfIxbSNbEUSmeI48EpaqIHMFYhVdZMOY5PkSOK8j8eShATUQGYx1+K4KRkBFQd7CySF45QbrjPEKfB7i4j85lRTwpuLNQ3D30yZSb+6bxAucuJNcYPkmo/Qa8qpLRUxYCv4N7+6Zswlw2Kxx5GtClTZEjGBvKQuC3cAvJmjgmD8exxgJBqbZEjmDcVhYCJ457yHEFuS7k6O6IyGD0lYXAEVB3kAR+O91xbqLPLi5uoLMWuKaRCtxnU9lg+fihm4Tk9QbGXTEVX6eCGei7uoLsScH31iEnufMSW0Kma0AzlIq4ne5QNdUWLgJ01kXUNNIiNthUNiW2iY54sYExYWAsIumBH7UN4by7vw45qY+xiHQHcNrrImLrHBIgyQhn7lIRNY20iHh9dEAc0FoUqGiFDVsKqIuWBkSt64D4DdD6pkuD/V4KqGuRBkTKzRIGvIseO+hJTcc5pAc7xmu6+N8MCqlJ379/iYwfsg==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIb2MuJaWtlCoJURLlEnFYzJpaBRvZJg9FSPyzXvuzOmuvYTGE0EhF8u5845lvHrsDtDuX35cQQp+KCH1dRejHRf30AZMoojGuPOAbGkZe4OMKVksylnCb+jQkcRCCxiEtgHggYXoXhyRi9lwHUo+EwIAVR7UpUQ2rqZlOS7csm5qGpZV1Wy/rZdnVbMVoGoaqlW2DGi51dEN2ZYc0Hc0gmmod3NgQthn0/VYSoOsB+7VckiX+NCTcJXeg0wFDkpIGOsjJJQ6tBZ0gBLdrMKKQWK3DCmMK7Ac+BWKXkrgfMikmPmQegxRR0oWtRXmxNEuAhPcggucJ8T036LQA9f1ffnDr40YaMcmx2XddGl559BZXNAk74AutMAdpElMm+shEUbnNWeD58ZShMTJU9cEATCMHMsQVmUussAfIr5VI0IWGNELQORr9TGzDICZxcqjXB3LJki1FNq2yopRNQ7dMadzb/KsGi0M6ND0AWZGENQnGQvBovdDrerF3wyGJ49Br9uO0+rP6xdHlUf00See0fn5yeAzFQ0Ge3/KSJqpABplDcwYJM3FAHaWHOdkUmTWl24Pj9OPL+x64GIpqCp2Co03U+OpbTYMTW/QGTYZR/i1M3l2d5T6+G5o5cr+oHR4fnmNW9JhALDspuXkf07rrJlPK4TH12zGcsFous9scttk7TbdNdZzMlG9i/GxvA67LhLelTjprc5xNPRfaBjKfdOnoq6WUTtQcDkvXJzkUW82TpDM3h8TWzBk9mCbhQzk+GX4qYnhVZRd2f3//zwpCX45O0exPtZoJojLVDwVdJo/0VUFfFXhy9vlYIo8YV+ScymEG/5QveoQnFyufg8g//M0IOM8i8ucs6GLywryZPHxGjGHOf56McnXMlQuI/ToX0DLsq2gFrcG6htbRFtpAm7AWYX+FXqBtWLfRS/QW7aDXsL6B/T16h3Zh3UV7wJB+1vi+xOUML8OzLWC4vsAxxvDXAH0QMNs/Crgg8Bb4viTgZc6Z4RXum2HGvyFg9q4o4HWeX4aZ7UsBb8KzI+Aizz/DW/DsCphx7Qn4iTmdcZ8fm9+F5wU9MRePzPtTc/d/vgf+Au3g8V0=', 'selectors': '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'}]


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
