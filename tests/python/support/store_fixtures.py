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
# under test. A new PART_VERSION or GEOMETRY_SCHEME, or another producer, names another part:
# derive them again.
FIXTURE_PRODUCER = {'build123d': '0.11.1', 'ocp': '7.9.3.1', 'cadqueryOcp': '7.9.3.1.1'}
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': 'fcb6fc1834b2725f686e1d76410a7eb082d852fffeb4df0f2289361703cae663', 'payload': 'eNq1Vt1uEkEU3n9gS+kPtOBP7dgrJV4oJV7abaiJFyY1gXi/oYhNyC4BMalXPocv4QuY1LfwNRqfwClwZmFmzs7sEjdkdzJz9jvf+c43s/iX40FEOufdzvnFW9KLx/EoHt6Qj+0X5Fn/OVmshtN+eDXw38f98Mt1HE3JS78zm3wdtK6mpNX2TUN63Z3BSDegGCCBtwhCgsQH/M7LAU2RnYMqBSxk1yF5Y1MdUA5KHfQ55BYK1eFs424KCMHGHASE+R6ZklctX8FGuz5G5ruq3iBnBnZhGbC2oxn4F5Q1sIkgZwZlDTgiD6Dog/qFIGuGQJXhQzy6GcbR6QU9hZfjy6g3uQ6j4WzEzufubPIp7FP3vfYzF60Xd3erqarYhvQEmbugD5R39/yvCrID5dydmzdZcJjf634Ox9Rhp22/8O7nrx9//3x7gzfKNEz6ayoiF6XMIz2INOcTclzLRP1HFy36sNIY3b98AnNPl0+UYyBUs8ZRnskILBvfI4Hl0LuNLDKOxzB1oskR09HGpHLTdPTow1HpyDgeAce1zA4mQCFNnSK9uyp1joQOuoviDZOAFk8g5jEMHkGwYzCeiXYLxz1E5JZ90+QGl21IWWNcTPsSfXjYoq9j8AbM1VXVKAzuYV3aQjnSxbKOwQ9gqqHJEdOxgEm1naZjRcfgjGNVavAiJsBOmjq7OgavCh1kBj8ELWoQsw+DPdHgHm/wXWkpJeTbYO2hfqOL+2gp981bllIX9upaZh8Dr6ZlrqEWW8ncEM5QJmIFRAE1jW0YEFFEixexLC1lC2N7gJqNLh7qiFhNP2rLGHg9LXNDR0S2B44FEaF1JjNgiT9zV0R0eBGLAiAc0IkpwNFGRQR0eUBPAIRvQOLvGt/vFUCbBwTKTRsAH0DEDkSypsM5lBzsgNe04N8MGKnJvn//AGtuNXk=', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjID/yAS0lpK6VKIEqiXCIOa3uhVsFGxuShCIl/1mt/VmftNSzmERqpSN6dbzzzzezMDtBqX31fQQh9yiP0dR2hH5f12iMmvR6NcPkR39Kw5wU+LmO1IGMJt6hPQxIFIWgc4gLEAwnT+ygkPWbPdSB1SQgM2HBdy1HVkkVNXdeoZTua46qOa9ol09Rsx3ZdR7fckuIoLnE0jcqWrRVLTcVUdE1Wi0e3CotrB33fjSN0PKC/kQuyxJ+GhDvkHnQKYMhS0kAHSTWJQ6tBOwjB7QaMKGRWbbOTMQX2A58CcZOSqB8yKSI+pB6B1KOkA5tL+WlpmgAJH0AEzzPie82g7QLq+7/84M7HjSRinKPdbzZpeO3RO1zWJOyAL9TCGCRJTJkURyaKym3OA8+Ppgz1kaFaHAzAtOdAhrgsc4kd7BHyc2MJqtCQRggqR3s/Y9swiEgUd/XmSC6YsqnIhmkpimXoRdOQxrXNvmqwOKRNkwbIiiSscTAWgkfrhl7Hi7xbDkkUhZ7dj5LTn9cvT65O6rU4nVr94uz4FA4PB/J814uLqAIZZA7FGcTMxAF1L2nmZFFkVpROF9rpR1cPXXDRFdUQKgWtjdX4+ltVg44te4Mmwyj/Fibrrs5yH98NzRi5X1aPT48vMDv0mEA8dnxk+yGi9WYzHlMOT6nfiqDDqmWx2xy22DuYJUMdJzPlGxs/21uH6zLhbaqTztoCZ6OYCV0CMp906Oi7pZBM1AIOs1ic5FBKapYkmbkFJCXNmFGDaRI+lOPO8K6I4VWVXdjDwz9rCH05qaHZn0olFURloh9+HqtSeaSvCPqKwJOxz8YSecS4IudUDjP4p3zRHJ5MrGwOIv/wNyPgPMvIadzhcvLSvKk8fEaMYcZ/kYzm1G+mnEPs1zmHVmFfR2toA9YNtIl20BbahjUP+yv0Au3CuoteordoD72G9Q3s79E7tA/rPjoAhuSzwfcVLqd4FZ5dAcP1BY4xhr8G6IOA2f5RwDmBN8f3FQGvcs4Ur3HfFDP+LQGzd3kBb/L8UsxsXwp4G549Aed5/inegWdfwIzrQMBPzOmM+zxvfpeeF/TEXMyZ96fm7v98D/wFFMnyhg==', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'e73f9c7064e78b8d50afc51e95188e7a552111e4ec198de39df346f0eefeb0bb', 'payload': 'eNq1Vstu00AU9TuOm6aPpE14lA5d0YgFpBFLmCpFYoFUpETsrTSESpEdJQ1SWfEd/AQ/gFT+gt+o+AKGpHfceHw9M26xIns0c33uueeeGSc4nQwj0j3udY9P3pJ+PInH8eiSfOw8J88Gh2S5Gs4G4dkweB8PwovzOJqRF0F3Pv0ybJ/NSLsTmEbmdf0GRqoBPkUCrxCEBCkd8KsoBzSFPgdpClpYB3pvOtDCOlBNHfSF8um9SS1FoHfmICAs9siMvGwHEjbK9XEy3xTbqp2BX2gGqlsD1a2B6tZAdWswZPYBAEkf5C9Q3QxUluFDPL4cxdHRCTuFb8anUX96Hkaj+Zifz7359FM4YO57FWgXrRZ3faWoqtiG/ATaXVAHKrp7/lcF+kAFd+fdmyw4LOj3PocT5rCjTlB69+Pn9z+/v77GG2UaJvu1JJHLUhaRHkSai4lsXMtE/ccWLfaw8hj9e/kA5p7ePFGOVKhmhWN2JoNaNr5HqOWwu40sco77MHWgyBHT0cakcvN09NjDkenIOe4Bx5XMDiZAKU8dn91dmTp7QgfdZfGGSUCLJxDzGAaPINgxOM9Eu6XjHmJyU2WDUzWDu5j2ZfbwsMVAxeBNmGvIqpEY3MO6tIZyZIsVFYPvwFRTkSOmYwmTaj1Px6qKwTnHWqbBfUyAjTx1NlUMXhM6yA2+C1rUIWYbBluiwb20wTczSyljbLdQv7HFbZVSGsJeXckcYOC1vMx1FYs1hTOUi1gFUUBNYx0GRBTRSotYySxlDWO7g5qNLe5q+SH7qK1g4I28zE2tfboviAitM7kBy+kz95aITlpEXwCEAzoxBTjaqIqAbhrQEwDhG5D4u57u9y1AOw0IlFs2AD6AiA2I5E2Hcyg52AGvZcG/GTBSi3///gJ/Fx4i', 'glb': 'eNq1Vltv2jAUNr2Xdlu3dd2epspPe0hRbiTAy+jYJnVqoWqrvlQ8mMRh0SBBSehFFRL/bK/7WTtOHDDhUlZpSLbPZ5/znYt9gHbn6vsKQuhTHqGv6wj9uGzUHzEJQxrhyiO+pUHo+h6uYLUgYwm3qUcDEvkB7FjEBogHEqb3UUBCps/3QOqRABhwuURIq2WbNrVNXTVlx6FE00yLKLpNLUc2VZ2WDF23iFl2qKkT21BoyyzKZceULcU8ulWY35bf9+zYQ9cF+hu5IEt8NCXcJfewBxFKbGiwB0E5xKI1v+MHYHYDShQiq3VYZmwDe75HgRjCifoBkyLiQegRSCElXVhsyrOlaQAkeAARLM+I5zp+xwbU9355/p2Hm4nHOMZWH9IMrl16hyuahC2whVoYgySIKRV9pKKoXOfcd71oSrE4UlT1wQBUQwsixBWZSyyxR4jPjiWoQlMaIagcDX/GuoEfkSi+1ZsjuWDKpiIbZklRSkZRNw1pXNvsUZP5IR2aXICsSMIcO2MuuLde4HbdyL3lkERR4Lb6UZL9eePy5OqkUY/DqTcuzo5PIXlIyPVsNy6iCmQQORRnEDMTC7bD5DIniyKzonR7cJ1edPXQA5OiohpCpeBq4218/a2mwY0t+4Im3Sj/5iZrrs4yH78NzRiZX9aOT48vMEt6TCCmHafceohow3HiNuXwlHrtCG5YLZXYaw7a7EzTy4Y6DmbKNlZ+tnURnsuEtalOGmsLjA0947oMZB7p0tF3SyHpqAUcpq5PcihlNUuS9NwCkrJmzKjBNAlvyvHN8FsR3asqe7CHh3/WEPpyUkezP9VqKoibfD+zN9IXz6qCPprWn/JVneO3uiCGWfxZWzSHJ+NrKgaBf/gb5GFytpT8mfMOl5OX5k3l4TN8DDP2i2SUyWOhnEPs1zmHVmFdR2toA+YNtIl20BbahjkP6yv0Au3CvIteordoD72G+Q2s79E7tA/zPjoAhuSzwdcVLqd4FcaugOH5AscYw18D9EHAbP0o4JzAm+PrioBXOWeK17htihn/loDZWV7Amzy+FDPdlwLehrEn4DyPP8U7MPYFzLgOBPxEn856z/P6d9l+QYv7Ym6/P9F3/+d74C872uc1', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': '5f2419d322bf880e2fd49fd4209cc09782bbdedc00ed5aa26e964d16ae3233ac', 'payload': 'eNq1Vstu2kAU9RtwCHlAAn2kmbJqUBctQV22jkilLiqlEqh7ixAaCdkISqR0le/oT/QHKqV/0d+I+gWdQu4YPHNnxo5qIXs0c33uueeeGeOfTYYR6Z70uien70k/nsTjeHRNPndekheDI7JcDWeD8Hzof4wH4dfLOJqRV353Pr0ats9npN3xTUN43b2DkW5AMUACbxGEBCkd8CsvBzRFdg6qFFBudh3QgMw6oByUOuhzyC1U5hT6VXAIwYM5cAiLPTIjr9u+go12fYzMjareIGcGdt1k1APNkH5BWQMLCHJmUNaAI6YBFH1QvxBkzRCoMnyKx9ejODo+pafw/fgs6k8vw2g0H7PzuTefXoQD6r43fuai9eLubjVV5dsgT5C5C/pAeXfP/6ogO1DO3fnwJnMO8/u9L+GEOuy44xc+/Pj5/c/vb2/xRpmGSX8tReSylEWkB5HmYkKMa5mo/+iiRR+WjNG/l5sw9/z+iXIMuGrWOIozGYFl43sksBx6t5FFxvEQppqaHDEdbUwqV6ajRx+OSkfG8QA4rmV2MAEKMnWK9O6q1DngOuguizdMAlo8g5inMHgCwY7BeCbaLR33GJFb9E0TG1y0IUWNcTHtS/ThYYu+jsEbMFdXVaMwuId1aQPlSBfLOgbfg6mGJkdMxwIm1aZMx4qOwRnHqtDgRUyALZk62zoGr3IdZAbfBy1qELMLgx3e4F7a4NvCUkqYTjsSv1m7aCkrIta5vbqW2cfAq7LMNdRiom3Q5ESsgCigprEJA8KLaKVFLAtL2cDY7knMZu3riFiVH7VlDLwuy9zQEZHtgUNORGidyQxYSp+5KyI6aRGLHCAc0IkpwNFGhQd004AeBwjfgMTftXS/VwDtNCBQbtkA+AgitiCSNR3OoeRgB7yWBf9mwEgt9v37CwRZHuo=', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIL2zMpaS0lVIlIUqiXCIOi72mVsFGxuShCIl/1mt/VmftNSw2ITRSkbw733jmm8fuAO3O5fclhNCnIkJfVxH6cdE4fcCk36cRrj7gGxr2vcDHVayWZCzhNvVpSKIgBI1NHIB4KGF6F4Wkz+y5DqQeCYEBG3LFlImuOg41LNu2WmW9YitlYtMWtdSK68qqrWnEtDVQWUSXbVU2KrrpGLos6wY9uFFY3FYw8J04QtcD+mu5JEv8aUq4S+5ApwGGLCXYm5CUC4T1oBOE4HYNRhQyq3dYZUyB/cCnQOxSEg1CJkXEh9QjkPqUdGFzKK+WpgmQ8B5E8DwhvucGHQfQwP/lB7c+biYR4xxbA9el4ZVHb3FVk7ANvtALY5gkkTPRxyaKym3OAs+PcoblsaGqD4dg2rchQ1yVucQKe4D8nFiCLjSlMYLO0f7P2DYMIhLFp3p9IJdM2VRkw6woSsUo66YhTXqbfdVkcUiHJgcgK5KwxsFYCB6tF3pdL/JuOCRRFHqtQZRUf9a4OLo8apzG6Zw2zk8Oj6F4KMjzHS9uogpkkDk0ZxgzExvU/eQwp5sis6Z0e3CcfnR53wOXsqIaQqfgaGM1vvpW1+DEFr1B02GUfwuTdVdnuU/uhmaM3S/qh8eH55gVPSEQy45Lbt1HtOG68ZhyeEz9dgQnrFYq7DaHbfZO0y1DnSST842Nn+1dhusy5W2q087aHGdDz4S2gMwnXTr+biklEzWHw9T1aQ7FUrMkyczNIbE0Y0YP8iR8KCcnw09FDK+q7MLu7/9ZQejL0Sma/anVUkFUJvpaLW831gsYZWyneDM6kUeMK3LmcqjN5kO1fKwcTyZWNgeRf/Qb5FHybiH5M+cdLSYvzJvKo2fEGGX858koU8dcuYDYr3MBLcO+ilbQGqxraB1toQ20CWsR9lfoBdqGdRu9RG/RDnoN6xvY36N3aBfWXbQHDMlnje9LXE7xMjzbAobrCxwTDH8N0AcBs/2jgAsCb4HvSwJe5pwpXuG+KWb8GwJm74oCXuf5pZjZvhTwJjw7Ai7y/FO8Bc+ugBnXnoCfmNMZ9/mx+V14XtD8uXhs3p+au//zPfAXNj3sZQ==', 'selectors': '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'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'd8101e2cb09e0874c0403df11cd5681f69bb1f24a4ccf2fb32b582469b5e87b8', 'payload': 'eNq1Vstu00AU9TuOm6ZukzbhUWqyohELSCOWMFWKxAKpSInYW2kIlSI7ShqksuI7+Al+AKn8Bb9R8QUMSe+48cz1jN1iRfZo5vrcc889M453Oh1FQe+43zs+eRsM4mk8iceXwcfu8+DZ8DBYrYbzYXg28t7Hw/DiPI7mwQuvt5h9GXXO5kGn6+ma8Lp+AyPVAJcggVcIQoKUDvhVlAOaIj8HWQqfFNXBJ/elA8pBqoM6h8JCueS+pJYjkDtz4BCWe2QevOx4EjbK9TEy32T1koIZ2IVl8EnOGtIvSGtgL5CCGaQ14IhpAEkf5C+QvBmILMOHeHI5jqOjE3oK34xPo8HsPIzGiwk7n/uL2adwSN33ystdtFrc9ZWiqnwbshPk7oI6UNHd878qyA9UcHfevcmcw7xB/3M4pQ476nqldz9+fv/z++trvFG6ptNfWxK5KmUZ6UCkvpwQ4xo66j+6aNCHkcXo38stmHt680Q5Eq6aNY7iTBoxTHyPEMOidxNZZBwPYKqlyBHT0cSksrN0dOjDkunIOO4Dx7XMFiZAKUsdl95tmTr7XAftVfGaHoAWTyDmMQweQbClMZ6JdivHPUTkFn3TxAYXbUhRY2xM+zJ9ONiip2LwJsw1ZNVIDO5gXdpAOdLFiorBd2GqqcgR07GESbWZpWNVxeCMY01ocBcTYCtLHV/F4DWug8zge6BFHWJ2YLDNG9xJG9wXllIW8vGJsY36jS7uoKX4SSkNbq+uZfYw8FpW5jpqMV+wDVqciFUQBdTUNmEQ8CIaaRErwlI2MLa7qNno4p6KiLXso7aCgTeyMjdVRGR74IATEVqnMwOW02fuLRGttIguBwgHdGIKcLRW5QHtNKDDAcI3IPF3Pd3vW4BmGhAot00AfAARWxDJmg7nUHKwA17bgH8zYKQ2+/79BYmMH7I=', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIb2MuJaWtlCoJURLlEnFY4zW1CjYyJg9FSPyzXvuzOmuvYTGE0Egl8u5845lvHrsDabUvvy8hhD4VEfq6itCPi/rpAya9Ho1x5QHf0KjnhwGuYLUkYwm3aEAjEocRaJrEBYgHEqZ3cUR6zJ7rQOqSCBiwZqi61XRtW7c007AJMU1HNhzH9TTXsCzL0GX4U4juqLZiEcdtaoZna4ZtqbZLHPvgRmFxnbAfuEmEjg/013JJlvjTkHCH3IFOBwxZShroICmPNGktbIcRuF2DEYXMam1WGVPgIAwoEHuUxP2ISTEJIPUYpB4lHdhcyqulWQIkugcRPE9I4Hth2wXUD34F4W2AG2nEJEen73k0uvLpLa5oEm6CL/TCHKRJTJnoIxNF5TZnoR/EU4bGyFDVBwMw7TUhQ1yRucQKe4D83ESCLjSkEYLO0d7PxDYKYxInp3p9IJcs2VJk0yorStk0dMuUxr3Nv2qwOKRN0wOQFUlYk2AsBI/WjfyOH/s3HJI4jnynH6fVn9Uvji6P6qdJOqf185PDYygeCvID10+aqAIZZA7NGSTMpAnqXnqYk02RWVM6XTjOIL6874KLoaim0Ck42kSNr77VNDixRW/QZBjl38Lk3dVZ7uO7oZkj94va4fHhOWZFjwnEspOSnfuY1j0vGVMOj2nQiuGE1XKZ3eaoxd5pum2q42SmfBPjZ3sbcF0mvC110lmb42zqudA2kAWkQ0ffLaV0ouZwWLo+yaHYap4knbk5JLZmzujBNAkfyvHJ8FMRw6squ7D7+39WEPpydIpmf6rVTBCVqX4o6DJ5pK8K+qrAk7PPxxJ5xLgi51QOM/infNEjPLlY+RxE/uFvRsB5FpE/Z0EXkxfmzeThM2IMc/7zZJSrY65cQOzXuYCWYV9FK2gN1jW0jrbQBtqEtQj7K/QCbcO6jV6it2gHvYb1Dezv0Tu0C+su2gOG9LPG9yUuZ3gZnm0Bw/UFjjGGfw3QBwGz/aOACwJvge9LAl7mnBle4b4ZZvwbAmbvigJe5/llmNm+FPAmPDsCLvL8M7wFz66AGdeegJ+Y0xn3+bH5XXhe0BNz8ci8PzV3/+d74C/ijvGO', 'selectors': 'eNqtWEuPozgQ/i+cycgvMOQ6+zjsYaWd0VyiaGVsk7BLIAKSndYo/33LDwgkJGHUrRakwXbVV5/rZX4Erdzrg/imm7aoq2Adh8GxqfOi1ME6EE1X5EJ2QRjoqmve/igqBa+P8B5eSXEUWVEWXaHbYP0j0GqnP5eibYu8kKKz4n4EotzVTdHtD7Cwlqus0ceVrKuuqE5F97Y6Y5Akqt2pFM3XutSNqKT+Re+CNQmDVhyOpRFOL/BwagCL/hW0/KUrpZui2t0qMBBWZVHp1gk+F21hIb5ZYEbUJsi16E6NhuEOFINh8F+rxQF+lIZnwNDpYAsqAWelZafVF13Cr7fI2rkXhTev2FUHEGIx00/oMoj9DdCCwq456Qu8zbL6u1lxMAs36BMK/bUNg4OAoQ2DZwIXSDHa2050lthaylPTaCDmc30CuGscBqUW+Z8z79u9OI4eDWP+CXbWIvdjQO9ZN53+7l8kFzc+4fY9/N2xbxVZg3oJgOJi+cpKfWdpeTpUVl8HPtHmdWMUWBK9lV8654cjk53B/cDVeGda//5KQ89BPzJmZDtIHpDUjSoqUcLEf00kDHAkkKCbn9a+7ScNCipxMLxe9VgEweD8X9/sUwVk2GGv/5FxEKji0F436vemPh2NWjfptVrYjXOvtNTVrtuPrfXaHxn9mNowaCCgzibUzZpS7NoxWqH+EdIHUL/gmjKMmxV7rRpRmpi7czMz2Ubn9qp0gaU3Rj3YMfDWq48aiZsNvkbycP3Eu224PCXAArhi80vglmx7H7VITPTXZQF+uVzihnhk+FO0HWRvvWdasUG+1oiSmFKcMRoHgAE0HUtRAW+blVm8GpQ804xubGEwGzL3bmaB+F4Y3T1lRj4swdvQosmiOFJaZQjTICRjNJMFAxj2kgY2BcMWgCEODOGIC5ZzFUugho7B2Mn4Ds2jTUEDmmQBNauRS8ES6tAAJQnJWMa5Bjdgk40aL5iAIU+pAU+7Q0NeoGEOjaAqz/JMcaxIEEZ3aFY9Q8tiwDtBvICdcXxdwsjBSWMmFaFY4BggxLdb9TAe6UNuyAMnpo+xxFuffl1s6TVCKkJKRjnTyMeWaWACI2QMLHodXGgUXMRelIfxE7ZU0fRtzQ3MoflSf+9FA76UmrFr6behqNdJTDXKc8VQinwoOvCkB497Op6xOs0MxP9R8gA8vQN/43+v0BOHXkiuUiY1kjH3sXtDPZmjnjzNaywk9pqlnnwA9dSBJ3FOU5WrmFHkQ32G+sVJ2REXg/b4GfXovdQz7zhcSp0QnSHep4YR9WzO69mCvEnsdUc9+yCvjxx4jQlREvOUsswnkhH1bM7r2ctcgk282tsd+eyj/D52+CPMqdapRFqlQchnyCdz5L+sE8TdZun/CM/nDr6MogzHmWIpYgFs+Dz9izsAX1ZMzNrbQ/rf7fuJw6+yNIl5Hkshc5hzxW8UbchY25KeYSiLxN0YXhy7ty3NC/xpnzZRBtVKYJEAfoweGECX1NJr8GITufb20AD6XgMwchakMUlikTHoB0w/i2csID/TKaG+GyDuNmsB+YgtwL7wKsQjjEWeSAU9MCYPLKBzFjzpZ2zhJc8seP8e2NOFOZgV/QHjvFYCJQhrlhGauCZouumvmodx52Pr43mNsNRZHnHOE+xak2kgLGmmfG/di6RUU+jTuEx06vqFKS3PiJ42CXEvkhHJcoaRjETkqvjUV5b0HQ5lOhguGRaIUYFy5ErrtIC8KklonNK9TBUxziOm87SveNOsuKRG+zwVDWxiFcP5gGubxnkvk8zhfO62kDV6mSmcN3IpSJppVxqmxWdJOfNujEHm1n6osJ8y3Xer/vtj/Z/t7EE+UATbBhUPmDbnttQcoG0px2aXzf/cAknNVmDf/JsvG4MQ06pHxnHtmRhbXzXbZhys7yYjq4aaDsQK+GY/bHgR5qOAbRzsUphkz7OxR8WtOGSXc9PfDd9FbkxJLYbEIXCWYbMsghFmrYjM1mMrGVLp5fI/2SYTOg=='}]


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
