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
# what ``cadgen.store.parts`` derives from ``payload`` under FIXTURE_SURFACE_PRODUCER
# (test_store_seed_inputs.py holds them to it). Keeping encoded inputs here lets catalog and
# security fixtures seed genuine geometry and display data without importing OCP in the process
# under test. A new PART_VERSION, or another producer, names another part: derive them again.
FIXTURE_SURFACE_PRODUCER = {'build123d': '0.11.1', 'ocp': '7.9.3.1', 'cadqueryOcp': '7.9.3.1.1'}
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': '01e2a28516540a13ff82f3e0479e481eb16c24c2f60cad2cb2e518d90eecee41', 'payload': 'eJylVttu00AQXTvXuumFFih3DE8Q8QBpxCN1lSLxgFSkRLxbaRsqRXGUEKTyxHfwE/wAUvsX/EbEFzBOdmYTeyZrB6uKVzuzc87MnB3XOx2eD/zWcbt1fPLe70TDqB/1rvzPzVf+i+5Lf24Nx93w7Nz7GHXDr5fRYOy/9lqT0bfzxtnYbzQ9R7HP9AhXWR2qgeB4LUQwkZION+tyECHyc7BBoCF/HcwJG4ebdTlIzxocsjpkr0PuUtsjBP/NIRVhdkfG/puGdEOys1MJMj+SWAKZ3Aj0SAhS20WE5AFrDrSBjnkR6JEQ5IjJAJY+2A8EeRECG8KnqH/ViwaHJzCF9fp00BldhoPepE/zuT0ZXYRdUN9bm/jSCNn8pte4yi2l1QC5u5A9kETIdntSB/IC5x4AUiCJkHUoC4EsfqbJKYV5nfaXcAgKO2x6lQ+/fv/8++f7Oz5q3ChHOfBXt3jOU5l5ltHTmW3wcV1HSfoDowsvdxWj+PBz3Hum3yLHIJXNEkceSQVuQeQIxiL8FgQjcXyKW0jWxlEpniOPBKUqiRzBWIZXUTCmOT5GjkvI/HkoQEVEBmMVfkuCkZAR0HSwNE9eOb7ecZ6gzyNcPETnoiKeFNyZK+4B+iTKzX3TeIFzF5JrDJ8k1H4DXmXJ6KkMAr+Hewe2bIJUNksceRrQpU2RIxhrKoPA7+AWkrVxjB+OY4UFglJtiRzBuK0yCJw47iPHJeSqkKO7IyKDcVdlEDgCmg6SwO/qHec2+uzh4hY6G4EbGlrgu2wqGyyf6ZEbh+T1BsY9MZW4eToVzMDc1SVkTwq+vwo5zp2X2AIyXQOaoVTEbb1D1VRbuPDR2RTR0NBFrLGpbEpsYx3xYgNjzMBaRNIDP2prwnn3YBVyXB9rEekO4LQ3RcTWOSRAkhHO3IUiGhq6iHh9TEAc0EYUqGiFDVsIaIqmA6LWTUD8Bhh906XBfi8ENLXQAZFyvYAB76PHDnpS03EOmcGO8eou/jeDQqrT9+8fa241eQ==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIXhsbuJSUtlKqBKIkyiXisIY1tQo2MiYPRUj8s177szprr2Exj9BIRfLufOOZb2ZndoBW++r7CkLoUxahr+sI/bisVR8x7fVYiEuP+JYFPdf3cAmTnIoV3GIeC2joB6Bp0CZAPFAwuw8D2uP2QgdSlwbAgCkrWEbRdEjB0oipq8SxVcd0innTdEzddDRKVNVuUmI7RWIUdMs0bWZTrWEXrDy18ke3GoS1/b7XjAJ0XGC/UXOqIp66gjv0HnQaYEhS0UEHOTm0wSp+2w/A7QaMGCRWafODcQX2fI8BscNo2A+4FFIPMg9B6jHaga3JxGFZkgANHkAEzzPquY7fbgLqe788/87D9ThilKPddxwWXLvsDpd0BTfAF0phDuIkpkyMkYlGhM2573rhlGF+ZEiMwQBMew3IEJdUIfGDPUJ+zUiCKtSVEYLKsd7PyDbwQxpGTb05UnOWammqaRU0rWDmDctUxrVNv6rzOLTN4gaomiKtUTAeQkTrBm7HDd1bAWkYBq7dD+PTn9cuT65OatUonWrt4uz4FA4PB3K9phsVkQAZZA7FGUTMtAHqXtzMyaKovCidLrTTC68euuCSh4smVQpaG6nx9beKDh1b9gZNhtH+LUzancxyH98N3Ry5X1aOT48vMD/0mEA+dnRk+yFkNceJplTAU+a1QugwKRT4bQ5a/J0Os0fGyUz5RsbP9s7DdZnwtsiks77A2TRSoYtA5tEOG3215OKJWsBhGcYkh1YkaZJ45haQFHVzRg2mScRQjjsjuiKHJ4Rf2MPDwz9rCH05qaLZn3I5EWRlrB9+HqsSeaQvS/qyxJOyT8eSeeS4MudUDjP4p3zRHJ5UrHQOMv/wNycQPMvISdzhcvLSvIk8fEaMYcp/kYzm1G+mnEH81zmDVmFfR2toA9YNtIl20BbahjUL+yv0Au3CuoteordoD72G9Q3s79E7tA/rPjoAhvizIfYVISd4FZ5dCcP1BY4xhr8G6IOE+f5RwhmJNyP2FQmvCs4ErwnfBHP+LQnzd1kJb4r8EsxtX0p4G549CWdF/gnegWdfwpzrQMJPzOmM+zxvfpeeF/TEXMyZ96fm7v98D/wFMNTxuw==', 'selectors': 'eNqtWFuP6jYQ/i95hlXskAD7enpRVamVeo7OywpV3jCA25AgJ6S7OuK/d2Zi5w5kxWqVLInj8ffN3f7h5fEBjuo7mFxnqfcsZt7JZDudgPfsKVPonYoLb+ZBWpj333W6xdcnfI+vYnVSrzrRhYbce/7hwXYPXxKV53qnY1WwuB+eSvaZ0cXhiBOzeP5q4DSPs7TQ6VkX7/NSoCSV7s+JMt+yBIxKY/gJ9t6znHm5Op4SEh5c8OFsEAv8jKv8BekWjE73/QXomznhmCc6hbySXupcM853RkfyXrwdqOJsAIcLXB3Z4a8c1BH/bQGfEUgB3gbXRbApxAVsv0KC/y0tJntQ2nLU+/SIQhh48ORfarG/IGRcsDBnuODb19fsjWYcaeKL/+TP7LWZeUeFQy8CnyVeKIVWzwtVsHazOD4bA6idL9kZ4ZKlElC7P0fe5wd1aj2S2uxTNKuQ2zHUcQmmgDf7YnWpxjsKfkR/A+3zQkzISUAUF9bXawIDpsn5mPJ6eouST6o44L9UHWnhPDubGP6oHtAnEcRv9FWBPpTvMkNYWN9WIV+Lym9b2ql04wYaPVVacO8bjTl1uZG28ja15C7oJItVwsAaYtWj2epUJfjrX4qrcV6WQIzkwFCoGFC0MC0CH8a/cR9NhMiEemBtHH57P0GDp8aXot75Mwv8mh4NBlNJsU64E7XPKxuqY9641K8mO58IczXxAcz4QekQJ5Du2Y1qzBbqNVVeN/kdGmr7j4ptDnATmtRHkaIPsDUqobQxiBT6mBPMpln0ERX02F5xkEtbGC314mWURPFKz0nSunGm8nvXB95tZtPTIE7AK6L/Em+rjQs2h/ApJ5B8I7iUA7OElNTgnb4afhIyavEUbmaSBiKaYAFsbBTVa+9o2Z1b26GhSpqoFD2DJr84TbDMlzn9nNcwbmHze5pYzCLCgnnS6P3ILPWmc0vJt4vgPIGLMlZJWGUPq+xiFR2sHUk1VHFXjYsRqGICVOmgBgQ16EENaqhBpdbQOhRBZSligPWayf0a64rQhhPUOm95NM4LHNYFYV30sC6GWGXjAm1JHajyploxAsaxyjtYFw5rSFjDHtawxiod1hpcJWnutDstcq3fRYg2WE3QbDs1XGahAxsR2KgHNhoHGzDYmzkmuKpX6d9AGlxHGm1sharzARBSGMkH1JTWvuA3fnsv/P1W+Eu+FsEsWlxX6VYb16r20NZd9fbvgzKYINc01rRzLk8ApQgYyRMVB9lOacE9RXdTmLR/wfIKh2DAoefJ90i4DAKUPGAkg/QMIccMIW/m4QUZ4Zoh5CcYwmUWoKQCI5llxBCTa0mlxghBRLcM4T9qCJdygLINjKScliHEWESICZlb8jUwhPikiHCZCCgJwUgmahlCjEWEuJt6BIU03wamEJ8VE5GjsSQayx6N5cAUcswUdyuTrG6jxviMqFg6FitiseqxWI0aY3LbYisWhTXfrhrj4bhYORprorHu0Vg7GqLd4UzuaOrCK6ubXE0O734ndofG2tEQPlc8v1/y/FEmwZTC3ES4oPDm21UmwaNMhF9TqYr3oHqLARX5kd7Ndz2GtLfV5LrxUSp1CRdcw0W/iAs5SiUYo3KjXeIiLm9RedwqvNeiza9ubbeqQ71he9X1hnv9SLuncrW2JFWVw6anGzFTujW7O2gkUxNSDjuRrq5uab/bfkSNZGoNymF/0HWoKY1NhXndSKZ6XQ6Ldrcm3atyfrs81KKpjJbDWtrNsFOaAJvswkY0lbZyWN867jsoajdq86oRTfWmHBadbnWbUi+t+wsUveFDJD4ur45F3Rl39h/vNdCxUW9oUCxGqHzaqK7pGIJbBkGOQL+XOCBwIOTw5+0InTrVQmjXQFvGgA8QSKSgb2ly3cqGvExADQ8L+M6HTlYEnb9wg8JT8aNqe29RLVkcb6Dwt6Rmw55Z9aisGcOqQlAxEzQtxJEFswhRIPNaUlK+XP4H7UilqQ=='}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'ed085edcb64085e841fec251451ac621ec0789496dc57f816d1eadc03b56df33', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFE8mrlzzrl3ztzEO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZBAUUjbg93U1sBTFNRgpAibAXAcuoHgdOA3ccw0NtgH2dSheaiNC8N8acgiLOzLzX7W4G2KvTmTEfM9yMWIKM8iHZTAg5xkyG8w5BJl3UQb5sAwsYhbAcA7mDRlp1ht4ho/R6GIYjQ+PqQsn45Nxb3oejofzkezP3fn0LOyT+16bzJdnsIu7usSosJXWExQ+BXsgTpDp9uQ2FCUu3AA4IE6QsSkzQIY4dcg5h3m97udwQg47bHu19z9//fj759sbPWp8UI5w6NM0RC5TWURWEeksJvS4riM4/9GiSy93naJ481PMPUnerMYgl01Ko55JBG6J1UiLZfouMYtS42NMQaxJoxB6jXomKlWF1UiLVXqVmcW8xofQmGLW76cC1FhmWqzTd4VZlMwgVCdYWSYvHD+ZcR4h5gEG9xFcFlKnBHeWjruHmGy5g7SWNQbPRC7ANQejT5Jqv0GvKrfoCQuD38HcgSmbIJdNSqNeBp3SJquRFhvCwuC3MAWxJo3xo9NY0xJRqbZYjbS4LSwMLjXuQ2OKuc7k6O6wzLS4KywMDkJ1gtLgt5MZ5yZi9jC4gWBlcCUjMfiuNpUNTm0Myba5PZtUkIG6qylmjwPfX8cc5260mLwGsofKIm4nM7KaYgsDH8GqiEpGUsSGNpVNTm3sI7Ybxgrs/aBvtQ3tdgI/WMcc18f+nqLbqyLi6BxpQGkj9NyVIioZSRFxfRQgGrQyBRwtcGArgKpoCSC8rgDxG6D8LS8NznsFUNUiAYTkZgmAdxGxg0h56OhDqrEDr+ni3wyM1JS/f/8AfxceIg==', 'glb': 'eNq1VktvGjEQNnmHpG3apmlPVeRTDxu0L7wLl5LSVkqVQJREuUQczOKlq8IuWpY8FCHxz3rtz+p414BZHqGRimR7vvHMNzO2B2i2rr6vIIQ+ZRH6uo7Qj8tq5RHTbpdFuPiIb1nY9QIfF7GeU7GCm8xnIY2CEDQObQDEfQWz+yikXW4vdCB1aAgMmBgaMYjtENPKW7ZKNerW1bzqOI7u2qqmq6xh14lDC5bhkILtNCzVJrBJDGZQ2zKPbjUIWw96fiMO0PaA/UbNqYoYNQW36T3oIEGFDwN0kJNLHVYOWkEIbjdgxCCxcosXxhXYD3wGxC6jUS/kUkR9yDwCqctoG5YGE8WyYQI0fAARPM+o77lBqwGo5//ygzsf15KIcY71nuuy8Npjd7hoKNgBXzgK0k+SmDIxRyaaLmzOA8+PpgzzI0Pd7PfBtOtAhrioCokX9gj5NWIJTqGmjBCcHOv+jG3DIKJRfKk3R2rOUi1NJZataTbJmxZRxmeb3qrxOLTFkgtQNUWa42A8hIjWCb22F3m3AtIoCr16L0qqP69enlydVCtxOpXqxdnxKRQPBXl+w4sPUQcyyBwOpx8zUwfU3eQyJw9F5YfS7sB1+tHVQwdc8ppOpJOCq43V+Ppb2YAbW/YFTYbR/i1M2l2f5T5+GwYZuV+Wj0+PLzAvekwglx2XXH+IWNV14y4V8JT5zQhuWLdt/prDJt8zzALRx8lM+cbGz/bOw3OZ8Lb0SWdjgTMxU6ELQObTNht9teSSjlrAYZnmJIdW0NMkSc8tICkYZMYZTJOIphzfjLgVObyu8wd7eHj4Zw2hLycVNPtTKg0FWSn0Kd3IXt4rSfZo2n4qVmlO3NKCHGbxp33RHJ5UrKkcJP7Bb5AHyd5S8mfBO1hOXpp3KA+eEWOQ8l8ko1QdC+UM4r/OGbQK6zpaQxswb6BNtIO20DbMWVhfoRdoF+Zd9BK9RXvoNcxvYH2P3qF9mPfRATAknw2xrgh5iFdh7EoYni9wjDH8NUAfJMzXjxLOSLwZsa5IeFVwDvGa8B1izr8lYb6XlfCmyG+Iue1LCW/D2JNwVuQ/xDsw9iXMuQ4k/ESfznrP8/p32X5Bi/tibr8/0Xf/53vgL6Lc5Z4=', 'selectors': 'eNqtWFuPqzYQ/i88JyswgST7enpRVamVeo7OSxRVXpgkbglEhtBdHeW/d2aMuSchymoFWTCe+b4Zz8X+4eTRAY7yO+hcZanz6s2ck852KgHn1ZG6UDsZFc7MgbTQH7+rNMbXJ3yPryJ5km8qUYWC3Hn94UC8hy+JzHO1U5EsWNwPRyb7TKvicMSJWTR/03CaR1laqPSsio956aEkme7PidTfsgS0TCP4CfbOq5g5uTyeEhLuX/DhrBEL/Ixa/oI0Bq3SfV8BfTMnHPNEpZAb6aXKFeP8YHQkb+PsQBZnDThcoHZkh//lII/4EwM+I5ACnC3qRbApRAXEXyHB34oWkz1IVXFU+/SIQhi4/+JearG/IGRUWOgzXPDt21v2TjOONHHjvriz6trOnKPEoY3AZ7pQCmnPC1mwdbMoOmsNaJ0v2RnhkqcSkLs/R97nB3lqPZLZqqdwZpBXY2jjEnQB79WL1cWMdwz8jP0G1mdFTMhKQBQXttdbAgOmyfmYsj4Vo+STLA74k8ojKc6zs47gD/OAaxJB/EZfFbiG8l2mCQvbuzLI18Ks25Z1jG3sQGMnYwX7vrGYNZcdaRtvW0vugk6ySCYMrCFmHnWsUpngf/9SXI3zqghESA40hYoGSYpJCTyMf2s/mgiRCfXAVnH47eMEDZ4aX4p2588q4NfsqDGYSop1wp3IfW58KI95s6R+1dn5RJjNxCcw4welRZxAuudlVGOuoF4z5XWX36Eh439kVOUAO6FJfRQp6gCxlgmljUGk0MecYLaN0mdM0GN7ZYFc2sJI1cbJKInilZ6TpHXbeE32qq8H3m1n09MgTsArpF+Bt9XWBptF+JITSL4RXMqBWUJGavBO18bMzBVsZz4NeHSzCLZVGNXKd6R3Z5VbOFRKE5ni0ghx8sZtC93M6d95jeMWOLdnisUsIGtgotRqPzJLvitCZs1OSnCeh0oZqyCsoodVdLGKDtaOpBqquGvHxQhUMQGqsFB9gur3oPpdqF69oggqS/EGWK/53K2xrqaadd5a0jjPt1gXhHXRw7oYYhXNEmhL6kAVN82KITCOVdzBurBYA8Ia9LAGNdaFxVqDM5Lm1rrTQrdadyGtA3+CZdu54TILLNiQwIY9sOE4WJ/B3kwy/lW7CvcGUv860nBblag6HwAhhZF8QF2pQ7LaIIP74e+2wl/wtfBm4eK6SWOlba/aQ1u31fHfB6kxQ65prOnnbJ4AShEwkicMB9FOaf49Q3dTmKj+/OAKB3/AobeS75GwGQQoecBIBuk5Qow5QtzMwwtywjVHiE9whM0sQEkFRjLLiCMm1xJjxhBBhLcc4T7rCJtygLINjKScliPEWESICZlb8DVwhPikiLCZCCgJwUgmajlCjEWEuJt6PAppvg1cIT4rJkJLY0k0lj0ay4ErxJgr7lYmYW6jzviMqFhaFitiseqxWI06Y3LbUlUsCmu+XXXG03GxsjTWRGPdo7Hu0PDaSqd0NHXhFeb2QHj3O7E7NNaWhudyxXP7Jc8dZeJPKcxNhHsU3nx7oGI8yMRzayqmeA+qtzegIh7p3VzbYwhzG6UiPsMpXl3CPa7hXr+Ie2KUij9G5Ua7xEVc3KLyvFd4r0W7X9XabplTvWF71V0N9/qRdk9la21JpiqHTU83YqZ0a9XuoJFMTUg57ES6trpl/W77ETaSqTUoh/1Bd0FNaWwM5nUjmep1OSza3Zp0r8q57fJQi6YyWg5raTfDTmkCqmQXNKKptJXD+tZZvoOidqM2rxrRVG/KYdHpVrcp9bJa/h6K3vIpEp+Xm3NRe8id/cd7DVzYaDd06BLtE/BGdU3HENwyeLQQ6P8lDng4EHD483aEjp1qIbRrCGil8wECifToW5pct7IBq/Gp4WEB3/nUqRJB+03OWzwVP+LtfVihWrI4l6cvqausD616VNaMYWUQGGYeH2ngyIJZYDIxvJaUlC+X/wFZU6XG'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': 'bca29d8e668cef75b9fa10687b2f4b3eb1535e5c4b11619bd55255a24e503011', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFU8mrlzz7lnzlzXO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZDKoTNmA39flwEIU52CCQLnFdWADCuvAcuCea3CwDbDXobDU5gzBf3PIZVjckZn/qsXdEHt2IkPmexaLIVMYQT4cAqcHi5DdYKxBBhiQWAT5cAh8xmwCwzmYN2SoWW/gET5Go4thND48pi6cjE/Gvel5OB7OR7I/d+fTs7BP7nttMl8ewS7u6hKjwlZaD1D4FOwTcYRMtye3oShw4QbAJeIIGbsVk8gQpw455zCv1/0cTshhh22v9v7nrx9//3x7o88as3KEQ39NQ+SylEVkFZHOYkKf13UE5z9adOnlrmMUb36KuSfJm+UY5KpJcdQjicAtsRxpsUy/JWZRcnyMKZA1cRRCz1GPRFJVWI60WKVXmVnMc3wIjilk/X4SoMYi02KdfivMokQGoDrByrJ44fjJjPMIMQ8wuI/gspA8ZXJn6bh7iMnIrfum6Q2uu5C6g9EXSdpv0KvKLXrCwuB3MHdgqibIVZPiqKdBp7TJcqTFhrAw+C1MgayJY/zoONa0QCTVFsuRFreFhcElx31wTCHXmRrdHRaZFneFhcEBqE5QGvx2MuPcRMweBjcQrAyuaCQG39WWsqHlQzrFKTm/uXtsKSsiogJ1V1PIHpd8fx1yXDvX2fLXQPZQKeJ2MiPVFFsY+AhWIioaiYgNbSmbHNvYR5zZ3JiBUUTpB32rbTD73YN1yLE+RhHlHUC3VyLi6BxpQGkj9NwVERWNRERcH5UQDVqZAo4WOLCVhEq0JCG8rhLiG6D8LS8NznslodIiSQjKzRIS3kXEDiLloaMPqcaOfE0X/83ASE35/fsHBFke6g==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIL2zDpaS0lVIlIUqiXCIOa7OmVsFGtslDERL/rNf+rM7aCyw2ITRSkbw733jmm8fuAO3O5fclhNCnIkJfVxH6cdE4fcAkimiMqw/4hoaRF/i4itWSjCXcpj4NSRyEoHFICyAeSJjexSGJmD3XgdQjITBgzdVttay2DMcimlKmRpmqplnRiC47VFYM0zYc12o5smarGnXtsikDcCu65eqObVkHNwqEtYO+30oCdD1gv5ZLssSfpoS75A50GmBIUoK9CTm5xKH1oBOE4HYNRhQSq3dYYUyB/cCnQOxSEvdDJsXEh8xjkCJKurC1KC+WjhIg4T2I4HlCfM8NOi1Aff+XH9z6uJlGTHK0+65LwyuP3uKqJmEHfKEVxiBNImeij00UlducBZ4f5wzLY0NVHwzANHIgQ1yVucQKe4D8WokEXWhKYwSdo9HPxDYMYhInh3p9IJdM2VRkw7QUxTLKumlIk95mXzVZHNKh6QHIiiSsSTAWgkfrhV7Xi70bDkkch57dj9PqzxoXR5dHjdMkndPG+cnhMRQPBXl+y0uaqAIZZA7NGSTMxAF1lB7mdFNk1pRuD47Tjy/ve+BSVlRD6BQcbaLGV9/qGpzYojdoOozyb2Gy7uos98nd0Iyx+0X98PjwHLOiJwRi2UnJ9n1MG66bTCmHx9Rvx3DCqmWx2xy22TtNrxjqJJmcb2L8bO8yXJcpb1OddtbmOBt6JnQFyHzSpeOvllI6UXM4TF2f5lAqapYknbk5JBXNmNGDPAkfysnJ8FMRw6squ7D7+/t/VhD6cnSKZn9qtZEgKlN9rZa3G+sFjDK2U7wZncgjxhU5cznUZvOhWj5WjicTK5uDyD/8DfIwfbeQ/JnzDheTF+YdycNnxBhm/OfJKFPHXLmA2K9zAS3DvopW0Bqsa2gdbaENtAlrEfZX6AXahnUbvURv0Q56Desb2N+jd2gX1l20BwzpZ43vS1we4WV4tgUM1xc4Jhj+GqAPAmb7RwEXBN4C35cEvMw5R3iF+44w498QMHtXFPA6z2+Eme1LAW/CsyPgIs9/hLfg2RUw49oT8BNzOuM+Pza/C88Lmj8Xj837U3P3f74H/gLJzuxf', 'selectors': 'eNqtWFuPqkgQ/i884wS6BXVez16y2WQ32XNyXozZ9GCpvYtgGmRncuJ/36qC5q5inBhAaLr6++pO/3Cy6ABH9R1MptPEefVd52TSnY7BeXWUyfVORbnjOpDk5uN3nWzx8Qmf46NIndSbjnWuIXNefziw3cOXWGWZ3ulI5Szuh6PifWp0fjjixDSavRk4zaI0yXVy1vnHrPBRkkr251iZb2kMRiUR/AR751W4TqaOp5iEywvenA1igZ9xlb8g2YLRyb6/AL0zIxyzWCeQldILnWnG+cHoSN7a2YHKzwZwOMfVkR3+y0Ad8bIFvEcgOTgbXBfBJhDlsP0KMV4rWkz2oHTFUe+TIwph4PLFu9Rif0HIuGBuznDBp29v6TvNONLEtffiudWxcZ2jwqE1znYFHnil1bNc5azdNIrOxgBq50t6RrhkqRjU7s+R59lBnVq3pLbqLnRL5NUY6rgAk8N79WB5Kcc7Cn5GfwPt80JMyEpAFBfW11sMA6bx+ZjwenqLkk8qP+AlUUdaOEvPJoI/yhv0SQTxG72Vow9lu9QQFtZ3pZCveem3Le2UurEDjZ5KLdjnjcasuuxIW3mbWnIXdJxGKmZgDbHy1mx1omL89y/F1TivikCE5MBQqBhQtDAtAg/j39iXJkJkQj2wVRx++zhBg6fGl6De+bUK+DU9GgymgmKdcMdqn5U2VMescalfTXo+EeZy4hOY8YXCIo4h2bMb1ZgrqNdUed3kd2io7T8qqnKAndCkPooUfYCtUTGljUGk0MucYDbNos+ooMf2ioNc2sJoqbWTUhLFIznHceu09pvsVR8PPNu409MgTsAjpKvA03Jjg80ifMkIJJ8ILuXANCYlNXinr4bMAkaN1407pwF/STMqBJsqjOrFd7Tuzi5u4VApjVWCrhHi5LVVBQtdz+jvrMZxC5zXU8XcnRMOTJRG70dmqXdNyKzaaRGc5+OijFUQVtHDKrpYZQdrR1INVd7V43wEqpwAVViokqDKHlRZQ10RVLKVV0NlKf4A6zWbezXWJWIN5hPUOmu5NM6TFuucsM57WOdDrKJxgbakDlRxU60YAuNYxR2sc4s1IKxBD2vQdQEbAyywlDSz2p0WupXfhVMdtp0bLm5gwYYENuyBDcfBSgZ7M8nIq3oVt0JLXkcabqoSVecDIKQwkg+oK3VIVhtkcD/8vVb4Cz7kyg3n11W61cb2qj20dVu9/fugDGbIFY01/ZzNE0ApAkbyRMlBtFOavKfobgoT1U/KKxzkgEPPk++RsBkEKHnASAbpGUKMGULczMNzV/AxagjxCYawmQUoqcBIZhkxxORaUqoxRBDhLUN4zxrCphygbAMjKadlCDkWEXJC5hZ8DAwhPykibCYCSkIwkolahpBjESHvph6fQppPA1PIz4qJ0NJYEI1Fj8ZiYAoxZoq7lUmUp1FjfEZULCyLJbFY9lgsR40xuW2pKhaFNZ+uGuPpuFhaGiuiserRWHWMYTucyR1NXXhFeXqgVvQ7sTs0VpaG73HF8/olzxtlIqcU5ibCfQpvf3mLiXyWie/VVMriPaje/oCKeKR382yPIcrTA3XjUSp1Cfe5hvv9Iu6LUSpyjMqNdomLuLhF5Xmr8LcWff3q1udWuas3bK+63nCvH2n3VLbWFqSqYtj0dCNmSrdWfR00kqkJKYadSFdXt7TfbT/CRjK1BsWwP+g61JTGpsS8aiRTvS6GRbtbk+5VOa9dHmrRVEaLYS3tZtgpTUCV7IJGNJW2YljfOu47KGo3avOyEU31phgWnW51m1IvK/f3UfSGd5F4v7zcF7Wb3Ol//K2Bjo16Q4MuUD8Bf6iuaBuCWwafHIH+L3DAx4GAw58/R2jbqRZCXw0BeTpvIJBIn96lyXUrG/AykhoeFvCdd50qEbQBw3mLp+JL/HkfVqgWLM7j6QvqKutNqx6VFWNYlghKZj5NC3BkziwCFMi8FpSUL5f/AU0Tpms='}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'cbccb6aabd344d5ca5314286fa349e93ba29d7162354f7b55790f62395ac9f9d', 'payload': 'eJylVs1u00AQXju/ddPWtED5x3CCiAOkEUdwlSJxQCpSIu5W2oZKURwlBKmceA5eghdAKm/Ba1Q8AePEM5vYM9l1sap4tTM73zcz347rHY9PR0HnsNs5PHoX9OJxPIwHF8Gn9ovgWf95sLBG0350cup9iPvRl/N4NA1eep3Z5Otp62QatNqeo9jn6i2ubB3qoeB4KUTQkbIOv6/LQYQozsEE4YeCg7EOvuRQuA4iB+m5BgdbB/s6FC61OUL43xxyEeZ3ZBq8akk3xJ6dypD5nsUSyBRGoEdCkNouImQPGHOgAwYkEYEeCUGOmA1g6IP5QIaa9QEZ4WM8vBjEo4MjmMLp+njUm5xHo8FsSPO5O5ucRX1Q32uT+PIIdn5Xl7gqLKX1AIW7YB9IImS6PbkDRYELDwApkETIOJSFQAY/3eScwrxe93M0BoUdtL3a+5+/fvz98+0NHzVplKMc+GsaPBepzD2r6OnMN/i4rqMk/YHRhZe7jlFy+CnuPUnfIscwl80KRx5JhW5J5AjGMvyWBCNxfIxbSNbEUSmeI48EpaqIHMFYhVdZMOY5PkSOK8j8eShATUQGYx1+K4KRkBFQd7CySF45QbrjPEKfB7i4j85lRTwpuLNQ3D30yZSb+6bxAucuJNcYPkmo/Qa8qpLRUxYCv4N7+6Zswlw2Kxx5GtClTZEjGBvKQuC3cAvJmjgmD8exxgJBqbZEjmDcVhYCJ457yHEFuS7k6O6IyGD0lYXAEVB3kAR+O91xbqLPLi5uoLMWuKaRCtxnU9lg+fihm4Tk9QbGXTEVX6eCGei7uoLsScH31iEnufMSW0Kma0AzlIq4ne5QNdUWLgJ01kXUNNIiNthUNiW2iY54sYExYWAsIumBH7UN4by7vw45qY+xiHQHcNrrImLrHBIgyQhn7lIRNY20iHh9dEAc0FoUqGiFDVsKqIuWBkSt64D4DdD6pkuD/V4KqGuRBkTKzRIGvIseO+hJTcc5pAc7xmu6+N8MCqlJ379/iYwfsg==', 'glb': 'eNq1VktP20AQXt4QaEtbSnuq0J56MJG9dmwnl0LTVqICggBxQTms7TW1mtiR4/AQisQ/67U/q7P2Otk4IaRIjeTd+cYz3zx2J8lV6/z7PELoUwmhr0sI/ThrHN9j2u2yBNfu8TWLu0EU4homZRUr+IqFLKZJFIPGpR5A3Fcwu01i2uX2QgdSh8bAgFVdI5rruKbv6bSqO76t6bZHHOLpFc80qpWqWtU8w7Udi9imZaqmpdmuTnXiOLppkd1rDcI6US/00gDtANgv1bKqiKep4Da9BZ0BGJJUdNBBTj51WT1qRTG4XYIRg8TqLV4YV+AwChkQ+4wmvZhLCQ0h8wSkLqNt2DwmimV5AjS+AxE8j2gY+FHLA9QLf4XRTYibWcQ0R6fn+yy+CNgNrukKdsEXWmH2syTGTIyBiUaEzUkUhMmYYWVgSIx+H0y7LmSIa6qQeGH3kJ+XStCFpjJA0DnW/ZnaxlFCk/RQL3fVsqVaGjTd1jTbrBiWqQx7W3zV5HFoi2UHoGqKtKbBeAgRrRMH7SAJrgWkSRIHTi/Jqj9pnB2cHzSO03SOG6dH+4dQPBQUhF6QNpEAGWQOzemnzNQFdTc7zNGmqLwp7Q4cZ5ic33XApaIRU+oUHG2qxhff6jqc2Kw3aDSM9m9hiu5kkvvwbujmwP2svn+4f4p50UMCuey0ZOcuYQ3fT6dUwEMWXiVwwsS2+W2Or/g73aiaZJjMmG9q/GzvClyXEW+LjDrrU5xNoxC6CmQhbbPBV0s5m6gpHJZhjHJoVVIkyWZuCklVNyf0YJxEDOXwZMSpyOEJ4Rd2Z2fnzyJCXw6O0eTP3l4uyMpM/yDpcnmg35P0exJPwb4YS+aR48qcYzlM4B/zRY/wFGIVc5D5H35zAsEzi/w5DzqbPDNvLj88I8ZDwX+ajAp1TJXnEP91nkMLsC+hRbQM6zJaQetoFa3BWoL9FXqBNmDdQC/RW7SJXsP6Bvb36B3agnULbQND9lkW+7yQc7wAz4aE4foCxxDDXwP0QcJ8/yjhOYl3TuzzEl4QnDleFL455vyrEubvShJeEfnlmNu+lPAaPJsSLon8c7wOz5aEOde2hJ+Y0wn3+bH5nXle0BNz8ci8PzV3/+d74C+7fvE7', 'selectors': 'eNqtWFuvozYQ/i88kyMwlyTndXtRVamVuqt9iaLKh0wStwQiQ+gerfLfOzNgrk7CKkcREDCe+b4ZzwV/d4rkCCf5FXSh8sx59V3nrPO9SsF5daQu1V4mpeM6kJX6/XeV7fDxGZ/jo0Se5ZtKVamgcF6/O7A7wKdUFoXaq0SWLO67I9NDrlV5POHEPFm8aTgvkjwrVXZR5fui8lGSzA6XVOoveQpaZgn8BAfnVbhOIU/nlIQHV7y5aMQCP6OWvyDbgVbZYayA3lkQjkWqMihq6ZUqFON8Z3Qkb+PsQZYXDThconZkh/8KkCe87ADvEUgJzhb1ItgMkhJ2nyHFa0OLyR6lajiqQ3ZCIQw8ePGurdhfEDIqLPUFrvj07S3/RjNONHHjvXhuc2xd5yRxaBPivcADpZD2opQlWzdPkovWgNb5lF8QLnkqBbn/0/K8OMpz75bM1tzFbo28GUMbV6BL+NY8WF3r8YGBn7HfxPqsiAkZCYjiyvZ6S2HCNL2cMtandij5LMsjXjJ5IsVFftEJ/FHf4JpEEL/RWyWuoWKfa8LC9m4M8rms123POrVtzEBnp9oK5nlnMWMuM9I33raVPASd5olMGVhHrL7VO5XJFP/9S3Fl59UQSJAcaAoVDZIUkxL4Yfxb89JMiExoBLaJwy/vZ+jwtPgytDu/1gC/ZUeNwVRRrBPuVB6K2ofyVHRL6ledX86EuZ74BGZ8oTKIU8gOvIxazA3UW6a87fIHNOTuH5k0OcBM6FIfRYo6wk7LlNLGJFLoZU4w207pMyYYsb2xQK59YaRq4+SURPHILmnaO238Lnu1xw8827rz0yBOwCOmq8DTamuCzSB8KQgknwgu5cA8JSN1eOdr24gGtf8Sbd2I7gS9YRBsmzBqle9J794oN3ColKYyw6UR4+SN1xe6WdDfRYvjHjhvZIrQDckImCi1OlhmyW+KkBmzkxKc56NSxioIqxhhFUOs4QDrQFILNXxox9ACNZwBVRioAUENRlCDFqpPqthZXouVxfgTsLec7rVgVwg2imfYddFb0zgvMGBDAhuOwIYWsKJbBH1RA6zirmExCOxgxQOwoQEbEdhoBDZqwa4M1hZcLWlhzDsveJuVF9M6WM4wbT87XN3IgI0JbDwCG9vBBgz2bpoJbtpVeHeQBreRxtumSLUZAQgpWDIC9aUOyeqDjB4nAK+XAAQfwdKNw9sm3SltutUR2rax3v19lBpz5JrGuo7OZAqgJAGWTFFzEP2kFjwy9DCJieYXiBscggmH0Up+RMLkEKD0AZYcMnKEsDlC3M3EoSv4sDpCfIAjTGoByipgSS0WR8yuJrUZYwQR33OE96wjTMoByjZgSTk9R4S2iAhnpG7Bx8QR4QdFhMlEQEkILJmo54jQFhHhw9TjU0jzaeKK8KNiIjY0lkRjOaKxnLhC2FzxsDKJ+mR1xkdExdKwWBGL1YjFyuqM2Y1LU7EorPl00xlPx8XK0FgTjfWIxtrQCPstzuyWpi28oj6F/uzwHvdiD2isDQ3f44rnjUueZ2USzCnMXYT7FN58uskkeJaJ77VU6uI9qd7+hIr4kd7NMz2GqE9WKuIjnOK3JdznGu6Pi7gvrFQCG5U77RIXcXGPyvNe4a8t+v5VvQ+uel9v2l4NV8OjfqTfU5laW5GpqmnTM4yYOd1a83nQSaYmpJp2IkNb3bP+sP2IO8nUGlTT/mC4oOY0NjXmdSeZ6nU1LdrDmvSoynn98tCKpjJaTWvpMMPOaQKaZBd1oqm0VdP6Nli+k6J2pzavOtFUb6pp0RlWtzn1sln+Pore8j4S75jXO6Nmmzv/j781cGGj3dChWFLR+PSluqaNCG4ZfFoI9H+JAz4ORBz+/DlCG0+tEPpqiGil8xYCifTpXZrctrIRqwmo4WEBX3nfqRFBWzDcoPBUfIk/8OMG1ZLFeTx9SV1lu201orJmDKsaQc3Mp2kRjoTMIkKBzGtJSfl6/R/H1qai'}]


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
    derives it under FIXTURE_SURFACE_PRODUCER (its display mesh, selector table and
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
            key, glb = part_key(entry, FIXTURE_SURFACE_PRODUCER), inflate(fixture["glb"])
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
        note_document_tree(sha, tree_hash, kind="step", surface_producer=FIXTURE_SURFACE_PRODUCER)
    if model is not None:
        note_output(document, owner)
    return tree_hash
