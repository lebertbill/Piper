

Contents lists available at ScienceDirect

## Tetrahedron Green Chem

journal homepage: www.journals.elsevier.com/tetrahedron-green-chem

## Protecting group-free photocatalyzed O -arylation of quinic acid

Miguel A. B ´ arbara a , Nuno R. Candeias b,c,* , Luis F. Veiros d , Filipe Menezes e , Andrea Gualandi f,g , Pier G. Cozzi f , Carlos A.M. Afonso a,**

- a Research Institute for Medicines (iMed.ULisboa), Faculty of Pharmacy, Universidade de Lisboa, Av. Prof. Gama Pinto, 1649-003, Lisbon, Portugal
- b LAQV-REQUIMTE, Department of Chemistry, University of Aveiro, 3810-193, Aveiro, Portugal
- c Faculty of Engineering and Natural Sciences, Tampere University, Korkeakoulunkatu 8, 33101, Tampere, Finland

d Centro de Química Estrutural, Institute of Molecular Sciences, Departamento de Engenharia Química, Instituto Superior T ´ ecnico, Universidade de Lisboa, Av. Rovisco Pais, 1049 001, Lisboa, Portugal

- e Helmholtz Munich, Molecular Targets and Therapeutics Center, Institute of Structural Biology, Ingolst ¨ adter Landstr. 1, 85764, Neuherberg, Germany
- f Dipartimento di Chimica ' Giacomo Ciamician ' , Alma Mater Studiorum -Universit ` a di Bologna, Via Gobetti 85, 40129, Bologna, Italy

g Center for Chemical Catalysis -C3, Alma Mater Studiorum -Universit ` a di Bologna, Via Gobetti 85, 40129, Bologna, Italy

## A R T I C L E  I N F O

Keywords: Quinic acid Photoredox Polyols Coupling DFT calculations Photocatalysis

## 1. Introduction

Highly  functionalized  biomass-derived  molecules  can  be  taken  as interesting starting materials for fine chemical synthesis, from which the exploration  of  the  chiral  pool  stands  out  as  a  particularly  relevant example [1]. The amalgamation of the chiral pool concept into  biorefinery  operations  [2]  offers  a  distinct  advantage  by  leveraging  the inherent  enantioselectivity  of  naturally  occurring  compounds,  facilitating the development of novel catalysts [3] and engendering innovative  synthetic  strategies  [4].  This  synergy  eases  the  production  of enantiomerically  pure  valuable  chemicals  and  pharmaceuticals  [5], thereby  contributing  to  the  advancement  of  a  more  sustainable  and

## A B S T R A C T

This study presents a novel and environmentally friendly approach to the preparation of quinic acid-derived esters from photocatalyzed O -arylation with haloarenes. This study expands the quinic acid-derived chemical space from renewable biomass by harnessing the power of visible-light-driven photocatalysis under mild conditions without the need for protecting groups. A thorough screening of reaction conditions, including the choice of photocatalyst, solvent, base, nickel source, and ligand, led to the identification of the most effective conditions, these being 5CzBN as the optimal photocatalyst, and glyme-based nickel complexes as the preferred nickel source.  These  conditions  enabled  the  formation  of O -arylated  products  with  good  yields  without  noticeable formation of decarboxylated products. Computational calculations support the proposed mechanism for the O -arylation process, based on oxidative addition, anion exchange, and reductive elimination upon energy transfer from  the  photocatalyst  to  the  Ni(II)  species.  Computational  considerations  for  a  nickel-catalyzed  photodecarboxylative arylation mechanism suggest that the oxidation of quinate by the excited photocatalyst or other species derived thereof is considerably less favorable than a pathway only involving energy transfer to a nickel species.  The  research  provides  valuable  insights  into  the  mechanism  of  this  environmentally  conscious transformation.

eco-conscious chemical industry.

Quinic acid (QA), a secondary metabolite derived from the shikimate pathway, holds a ubiquitous presence in both plant and microorganism kingdoms. This cyclic polyhydroxy compound manifests as a sole stereoisomer in various plant barks, tobacco leaves, carrot leaves, apples, peaches, coffee seeds, and food wastes [6]. Viewed through the lens of sustainability,  endeavors  to  employ  QA  as  a  viable  raw  material  for generating benzoic acid [7] and other aromatics [8] have encountered obstacles due to the high cost of its bacterial production from glucose [9]. However, the recent advances in modifying microorganisms for the biosynthesis of quinic and shikimic acids offer a prospective avenue for broadening the chemical space derived from biomass [10].

* Corresponding author. LAQV-REQUIMTE, Department of Chemistry, University of Aveiro, 3810-193, Aveiro, Portugal.

** Corresponding author. Research Institute for Medicines (iMed.ULisboa), Faculty of Pharmacy, Universidade de Lisboa, Av. Prof. Gama Pinto, 1649-003, Lisbon, Portugal.

E-mail addresses: ncandeias@ua.pt (N.R. Candeias), carlosafonso@ff.ulisboa.pt (C.A.M. Afonso).

## [https://doi.org/10.1016/j.tgchem.2025.100070](https://doi.org/10.1016/j.tgchem.2025.100070)





a. Reported esterifications of quinic acid

\_OH

arbara et al.

- ОН

HO

Despite  the  extensive  exploration  of  QA  as  a  starting  material  in synthetic chemistry, which has yielded foundational directives for its synthetic manipulation [11], the high number of OH-containing functional  groups  within  its  compact  structure  presents  substantial  challenges in achieving selectivity. One of the often-used protections is the carboxylic acid conversion to an ester, which allows the modification of the hydroxy groups and facilitates the manipulation of the cyclic polyol. The structural features of QA, in which one of the chair conformations is described by the proximity of the carboxyl group with the 5-OH, make it very easy to form the lactone derivative known as quinide. While this is the most often used strategy when the protection of the 5-OH is also desirable, the single modification of the carboxylic acid to an ester is challenging. Indeed, the esterification of QA is typically performed in the presence of strongly acidic conditions using methanol or ethanol as nucleophiles and as a solvent, most likely through a quinide intermediate (Scheme 1a). Scarce examples from the literature report the use of the carboxylate salt as a nucleophile in nucleophilic substitutions with alkyl halides, namely benzyl chloride, or its para -nitro derivative [12], 6-bromomethyl-1,4-anthracenedione [13], or 6-bromo-1-hexene [14]. A literature  survey  shows  that  the  selective  manipulation  of  the  QA ' s carboxyl  functionality  towards  esterification  is  very  limited  to  introducing primary alkoxyl substituents, as coupling agents tend to lead to intramolecular esterification [15].

Photoredox catalysis has witnessed a remarkable renewed interest for synthetic chemists over the last decade [16]. Harnessing visible light to drive redox reactions is remarkable from a sustainable perspective [17], as the use of light is less energy-intensive, often requiring milder conditions  and  being  more  selective  than  traditional  methods.  Additionally, greener solvents, such as water, that is often associated with undesired reactivities, can be used due to their inertness towards open shell reactive intermediates [18]; the shuttle of electrons between species,  often  not  requiring  the  stoichiometric  reagents  to  effectively change the substrates ' oxidation states, greatly contributes to a better atom  economy  of  the  transformations.  The  appealing  properties  of photoredox  catalysis  in  regards  to  its  sustainability  has  motivated vibrant  research  on  its  use  for  the  manipulation  of  biomass  derived molecules [19].

The traceless extrusion of CO2 from carboxylic acids allows building a  myriad  of  C -C  bonds  starting  from  different  carbon  hybridization states  [20].  Only  recently,  the  extension  of  photochemical  decarboxylative arylation was significantly expanded to tertiary carboxylic acids (Scheme 1b) [21], after studies on the influence of additives by Dreher and MacMillan [22]. Instead, the formation of O -arylated esters from carboxylic acids and aryl halides using photoredox catalysis has been reported either intentionally [23] or as a side path for the decarboxylative light-mediated C(sp 3 )-C(sp 2 ) cross-coupling (Scheme 1b) [24]. The divergent metal-free photochemical pathway was also studied [25], whilst nickel ' s redox promiscuity was reported to promote the light-free formation of reactive species [26].

Considering the large abundance of QA (and its corresponding esters) in nature, it was envisioned that the light-driven modification of QA could provide an opportunity to generate an open shell reactive intermediate prone to functionalization. Of particular interest would be the possibility of selectively arylating the oxygen of the carboxylic acid moiety without resorting to any hydroxyl-protecting groups. The merge of nickel catalysis, using bipyridyl ligands, and photoredox catalysis has been  considered  a  unique  method  to O -arylate  the  carboxylic  acid without interference with the lactonization process (Scheme 1c). In this work, we set to showcase the O -arylation of QA, demonstrating that, despite the presence of the multiple hydroxyl functionalities, the nickel complexes are taken in the selective formation of a new C(sp 2 )-O(sp 3 ) bond. This work seminally reports the preparation of quinic acid-derived O -aryl esters, bypassing the use of phenol derivatives and thus avoiding the competitive formation of quinide lactone. The influence of several key  experimental  parameters,  such  as  photocatalyst,  solvent,  base, nickel ligands, and additives, was investigated. The necessity of protecting the QA ' s hydroxyl groups was also evaluated due to its utmost importance  for  the  valorization  of  natural  resources.  A  thorough

Scheme 1. a) Esterification of QA. b) Tertiary carboxylic acids in merged photoredox and nickel catalysis. c) Photoredox modification of QA.

![Image](Bárbara et al. - 2025 - Protecting group-free photocatalyzed O-arylation of quinic acid_artifacts/image_000003_cb62ce84b4eb87cfd900a843214b236a0c8926b5077749e421cb95e2d9a63861.png)

НО

HO

O..

arbara et al.

computational study on DFT calculations is also presented to justify the selectivity of the transformation by comparing the O -arylation with the photodecarboxylative arylation process.

## 2. Results and discussion

We  commenced  the O -arylation  of  QA  by  screening  the  photocatalyst,  in  the  presence  of  2tert -butyl-1,1,3,3-tetramethylguanidine (btmg) as a base, nickel bromide glyme and 2,2 ′ -bypyridine (bpy) to form the metal complex catalyst, and in DMSO to ensure the complete solubilization of QA. The initial choice of the photocatalyst was based on its ability to reduce nickel(II) to nickel(0), this result was expected according  to  the  redox  potentials  of  the  excited  state  photocatalyst. Impelled by the previous reports on the use of iridium photosensitizers and nickel complexes [27], and by the use of phthalimide to slow down the nickel-catalyzed protodehalogenation pathway of aryl halides [22], we started our screening by employing 1 mol% of a heteroleptic iridium (III)  photocatalyst  Ir[dF(CF3)ppy]2(dtbbpy)PF6  to  gladly  observe  the formation of the highly congested O -arylation product in 70 % yield (Table 1, entry 1). The same photocatalyst was deemed suitable for the combination with nickel chloride (entry 7), whilst its non-fluorinated congener provided the same product in a reduced 54 % yield (entry 2). The homoleptic Ir(dFppy)3  promoted the formation of the desired product in 61 % yield (entry 3). Notwithstanding the success of the use of iridium complexes, we decided to focus on organic dye due to their smaller  environmental  impact  when  compared  with  their  metallic counterparts. When using NiBr2.glyme as the transition metal catalyst, 4CzIPN and 5Czbn showed very similar results (64 % and 66 % 1 H NMR yield respectively, entries 5 -6). Despite the similar profile of redox potentials  of  cyanobenzene-based  photosensitizer  3DAPFIPN,  its  use resulted in much lower yield (25 % 1 H NMR yield, entry 4). Curiously, when using nickel chloride glyme, no reaction was observed (entry 8), whilst the pentacarbazole derivative penta-carbazolylbenzonitrile (5CzBN)  could  deliver  the  product  in  64  %  and  72  %  yields  from nickel  bromide  (entry  6)  and  chloride  (entry  9),  respectively.  These observations are in accordance with the MacMillan [23] proposal of the energy transfer phenomenon being the driving force of this reaction.

Table 1

Photocatalyst screening.

![Image](Bárbara et al. - 2025 - Protecting group-free photocatalyzed O-arylation of quinic acid_artifacts/image_000004_bc605dcb1a4a1c1213cee8942b55dc9dc13ed12fac75a209687ae007aa229232.png)

|   Entry a | Photocatalyst                       | X   | Yield (%) b *   |
|-----------|-------------------------------------|-----|-----------------|
|         1 | (Ir[dF(CF 3 )ppy] 2 (dtbbpy))PF 6 c | Br  | 70              |
|         2 | [Ir(dtbbpy)(ppy) 2 ]PF 6            | Br  | 54              |
|         3 | Ir(dFppy) 3                         | Br  | 61              |
|         4 | 3DPAFIPN                            | Br  | 25              |
|         5 | 4CzIPN                              | Br  | 66              |
|         6 | 5CzBN                               | Br  | 64              |
|         7 | (Ir[dF(CF 3 )ppy] 2 (dtbpy))PF 6 c  | Cl  | 57              |
|         8 | 4CzIPN                              | Cl  | nr d            |
|         9 | 5CzBN                               | Cl  | 72              |

Indeed, the energy of the lowest triplet state (ET) of 5CzBn is higher than the one reported for 4CzIPN [28]. Unfortunately, such energy data for ET of 3DPAFIPN is not available, nevertheless the photocatalyst was proven to be unstable under irradiation [29].

After  identifying  5CzBN  as  the  best  photocatalyst,  we  turned  our attention to the nickel source and its ligand. Keeping the 4,4 ′ -bis( tert -butyl)-2,2 ′ -bipyridine  (dtbbpy)  as  the  nickel  ligand,  different  nickel sources were screened (Table 2, entries 1 -6). Moving from NiBr2 (entry 2) to the glyme complex improved only slightly the yields (entries 1 and 3). Ni(acac)2 was observed to behave similarly to NiBr2 (entries 2 and 4) while changing to the air-sensitive Ni(cod)2 resulted in a decreased yield by 11 % and complete lack of reaction in the absence of nitrogenated ligand (entries 5 and 6). Similar reactivity was observed for the diketonate Ni(acac)2 (entry 2) in comparison with other nickel sources, which seems  to  point  to  a  high  propensity  of  the  QA  derivative  to  avoid decarboxylation under those conditions [21]. Screening of the nickel ligands  (Table  2,  entries  7 -11)  has  shown  that  the  unsubstituted bipyridine and its 4,4 ′ -dimethyl congener were effective in stabilizing the  metal  complex  to  promote  the  formation  of  the  desired  product (entries 7 and 8). On the other hand, the electron-rich 4-4 ′ -dimethoxy-2-2 ′ -bipyridine (4,4 ′ -dMeObpy) proved to be similar to dtbbpy (entry 9).  More  restricted  phenanthroline  and  neocuproine  ligands  were identified as detrimental to the reaction success (entries 10 and 11). The amount  of  photocatalyst  could  be  reduced  to  1  mol%  by  either increasing the amount of QA and base (entry 13) or aryl iodide (entry 14). Finally, the photocatalyzed O -arylation of the protecting group-free QA could be tuned to 85 % yield by increasing the amount of QA and base to 2 equivalents (entry 15). The need for ligand (entry 16), photocatalyst (entry 17), and light (entry 18) to achieve product formation was confirmed.

Upon  establishing  the  conditions  for  the  direct  use  of  protecting group-free QA in the O -arylation procedure, we set to study the scope of the  transformation.  Considering  the  lack  of  solubility  of  QA  in  most organic solvents, the easy-to-prepare tetraacetyl quinic acid ( TAQA ) was used  in  further  studies,  thus  allowing  screening  of  other  solvents. NiCl2 · glyme was kept in the scope evaluation as it proved to be a better nickel source when using QA. On the other hand, while other bipyridines

arbara et al.

Table 2

Nickel source and ligands screening.

![Image](Bárbara et al. - 2025 - Protecting group-free photocatalyzed O-arylation of quinic acid_artifacts/image_000005_548e7305d72bec9b15aec4001c05304280df2f033128a9742768edfd2ceeba17.png)

|   Entry a | Modification                                       | Yield (%) b   |
|-----------|----------------------------------------------------|---------------|
|         1 | X = Br                                             | 61            |
|         2 | NiBr 2 instead of NiX 2 ⋅ glyme                    | 60            |
|         3 | X = Cl                                             | 63            |
|         4 | Ni(acac) 2 instead of NiX 2 ⋅ glyme                | 62            |
|         5 | Ni(cod) 2 instead of NiX 2 ⋅ glyme                 | 11            |
|         6 | Ni(cod) 2 instead of NiX 2 ⋅ glyme and no ligand   | nr c          |
|         7 | X = Br, bpy instead of dtbbpy                      | 81            |
|         8 | X = Br, dmbpy instead of dtbbpy                    | 80            |
|         9 | X = Br, 4,4 ′ -dMeObpy instead of dtbbpy           | 69            |
|        10 | X = Br, o -phenanthroline instead of dtbbpy        | 56            |
|        11 | X = Br, neocuproine instead of dtbbpy              | 17            |
|        12 | X = Cl, 1 mol% of 5CzBN                            | 52            |
|        13 | X = Cl, 1 mol% of 5CzBN and 2 equiv of QA and btmg | 69            |
|        14 | X = Cl, 1 mol% of 5CzBN and 2 equiv of aryl iodide | 60            |
|        15 | X = Cl, 2 equiv of QA and 2 equiv of btmg          | 85            |
|        16 | X = Cl, no ligand                                  | nr c          |
|        17 | X = Cl, No photocatalyst                           | nr c          |
|        18 | X = Cl, No light                                   | nr c          |

were superior to dtbbpy, the latter was kept in the following task due to its solubility in a wider range of solvents. Upon tweaking the stoichiometry, different bases (entries 1 -5) and solvents (entries 6 -15) were screened for the arylation of TAQA (Table 3). Amongst the three bases considered, btmg and t BuOK resulted in similar yields of the desired product  (entries  1 -3),  whilst  Cs2CO3 resulted  in  considerably  lower formation  of  the  product  in  only  11  %  yield  (entry  4).  Besides  the noticeable effect of the base, the presence of phthalimide as an additive proved beneficial for the reaction ' s success, as in its absence, the product could not be formed in more than 18 % yield (entries 2 and 5). The effect of the solvent was a determinant factor for the reaction success since using less polar solvents was generally detrimental to the reaction yield, resulting in the product formation of up to 50 % yield for THF (entry 8). Whilst  the  use  of  DMF,  NMP,  and  DMA led  to  product  formation in similar yields, in the 58 -65 % range (entries 1, 10 and 14), the use of methanol resulted in no reaction due to the lack of solubility of the photocatalyst (entry 11). Using water as solvent was even more limited regarding the reactants ' low solubility. Acetonitrile leads to the formation of the arylated product in only 28 % yield (entry 13). Despite our endeavor to find an alternative solvent for the arylation of TAQA , which could  simplify  the  work-up  and  isolation  of  the  intended  products, DMSO remained the  best  solvent,  allowing  for  the  formation  of  the desired product in 72 % yield (entry 15), comparable to the procedure described  for  the  direct  modification  of  protecting  group  free  QA. Doubling the number of equivalents of TAQA allowed substantial increase of the yield, resulting in formation of product in 86 % (entry 16). Despite the extended conversions observed by TLC and 1 H NMR of the crude mixtures, isolation of the product invariably resulted in decreased yields due to their considerable water solubility and lack of stability in silica. Nevertheless, product 4a could be obtained in 45 % yield from 0.2 mmol of the aryl iodide after aqueous work-up, as determined by 1 H NMR using 1,3,5-trimethoxybenzene as internal standard.

The scope of the transformation  was further assessed  to  consider both iodo- (Scheme 2a) and bromoarenes (Scheme 2b), with the yields depending mostly on the electronic nature of the aryl halide. Unsurprisingly, the presence of electron-withdrawing groups in the aryl halide partner  was  beneficial  for  the  reaction  success,  a  limitation  already shown for photoredox decarboxylative arylation [22,30], attributed to the oxidative addition being the turnover-limiting step [31]. Besides the introduction  of  aryl  rings  with  one  ( 4a )  or  multiple  trifluoromethyl moieties ( 4b ), the reaction was also amenable to the introduction of aryl nitriles (in either oand p -positions -4c and 4g ), acetophenones (simple and α -brominated -4d and 4h ), a m -carboxylic ester ( 4f ), and polycyclic aromatic  moieties  ( 4e,  4i and 4j ).  Despite  our  multiple  attempts  to introduce electron-rich aryl partners (Scheme 2c), the reaction failed to provide the desired compounds. Similarly, the use of iodobenzene led to the formation of products with low yield, which was also verified for two dicarbonyl compounds (Supporting Information). Considering that the electronic structure of Ni-bpy species is highly sensitive to both ligands and the surrounding environment [32], employing other bipyridyl ligands [33] is an envisioned approach to expand the transformation to the  use  of  electron-rich  aryl  halides.  Notwithstanding  the  moderate yields  reported  in  Scheme  2,  the  preparation  of  quinic  acid-derived O -aryl esters, either using O -protecting groups or not, has not been unequivocally reported before [34]. Furthermore, apart from the simple preparation of methyl or ethyl quinate esters, the installation of other longer  alkyl  primary  substituents  starting  from  quinic  acid  usually proceeds in only moderate yields (30 -58 %) [12,13].

## 2.1. Computational study

Numerous  studies  on  the  nickel  photoredox  esterification  of  aryl halides support an energy transfer process between the photocatalyst and a nickel species [35], as initially reported by MacMillan [23]. In

arbara et al.

![Image](Bárbara et al. - 2025 - Protecting group-free photocatalyzed O-arylation of quinic acid_artifacts/image_000006_369c61c16a04a737a022c776f3f53296d06c4ba1b00d860d51679d953a755738.png)

Table 3 Optimization of TAQA photoredox esterification.

|   Entry | n equivalent a   | Modification                   | Yield (%) b   |
|---------|------------------|--------------------------------|---------------|
|       1 | 1 or 1.5         | None                           | 60            |
|       2 | 1.5              | t BuOK as base                 | 41            |
|       3 | 1.5              | t BuOK as base in DMSO         | 24            |
|       4 | 1.5              | Cs 2 CO 3 as base              | 11            |
|       5 | 1.5              | t BuOK as base, no phthalimide | 18            |
|       6 | 1                | Toluene as solvent             | 31            |
|       7 | 1                | EtOAc as solvent               | 21            |
|       8 | 1                | THF as solvent                 | 50            |
|       9 | 1                | Acetone as solvent             | 35            |
|      10 | 1                | NMP as solvent                 | 58            |
|      11 | 1                | MeOH as solvent                | n.r. c        |
|      12 | 1                | THF/MeCN (1:1) as solvent      | 56            |
|      13 | 1                | MeCN as solvent                | 28            |
|      14 | 1                | DMA as solvent                 | 65            |
|      15 | 1                | DMSO as solvent                | 72            |
|      16 | 2                | DMSO as solvent                | 86 (45 %) d   |

order  to  ascertain  that  possibility  when  using  QA,  we  undertook  a density functional theory study at  the  BP86(PCM-DMSO)/(SDD, 6-31 + G**) level of theory. A full account of the computational details is presented as Supporting Information. The choice of the BP86 method is justified by the good description of the spin-state splitting for first-row transition metals [36]. Intrigued by the lack of formation of decarboxylative  arylation  products  and  considering  that  different  mechanism pathways can be followed depending on the nature of the ketyl radical or subtle changes in the nickel ligand [21,37], the O -arylation mechanism was  compared with  a  hypothetical  decarboxylative  arylation  mechanism based on the literature precedents [21,30 -32,38] (see Supporting Information). Moreover, 5CzBN has scarcely been considered a competent photocatalyst, and to the best of our knowledge, this is the first report on its use for the O -arylation of carboxylic acids.

The study started by considering the formation of a Ni(0) species from Ni(II), analogously to the process with iridium complexes as photocatalysts [27,35b,39]. The nickel ligand dtbbpy was replaced by the simpler 2,2 ′ -bipyridine for the sake of computational cost, and 4-iodobenzotrifluoride was used as the haloarene partner for the energy profile presented in Fig. 1. The oxidative addition step was determined to be exergonic ( Δ G = 26.1 kcal/mol), with a small energy barrier of only 1.9 kcal/mol to reach TS1 ‡ . This results from an electron-rich metal and a coordinatively unsaturated species combined with the electron deficiency  of  4-iodobenzotrifluoride  and  the  rather  weak  carbon-iodine bond. Bringing the btmg and QA pair close to the square planar nickel (II) complex 6 is unfavorable due to the entropy contribution (by 7.6 kcal/mol).  Unsurprisingly,  the  proton  exchange  process  between  QA and btmg is also exergonic with Δ G = 14.4 kcal/mol, measured from 7 to 8 . It starts with transition state TS2 ‡ , in which the nickel complex is merely a spectator species in a process with a negligible barrier of 1 kcal/mol at the electronic level. TS2 ‡ is  characterized  by  the  proton transposition  from  QA  into  the  sp 2 nitrogen  of  guanidine,  while  an additional hydrogen bond of 1.82 Å, established between the α -hydroxyl and the sp 2 nitrogen, stabilizes the transition state. The carboxylate of species 8 is  stabilized  by  an  intramolecular  hydrogen  bond  with  the α -hydroxyl group and taken by the nickel complex for anion exchange with iodide. The energy barrier is again small, of only 4.5 kcal/mol to reach  the  trigonal  bipyramidal TS3 ‡ ,  having  the  incoming  and  outcoming ligands at equatorial positions at an angle of 102 ◦ . The transition state TS3 ‡ is characterized by the establishment of the Ni -O bond (dNi-O = 2.13 Å) and elongation of the Ni -I bond (dNi-O = 2.53 Å in 8 vs dNi-O = 2.76 Å in TS3 ‡ ). Removal of the guanidinium iodide derivative from the mixture results in a stable quadrangular Ni(II) complex, S 10 ,  that requires  35.9  kcal/mol  to  overcome  the  reductive  elimination  energy barrier, corresponding to the rate determining step. When considering an energy transfer pathway, which involves the decay of the excited triplet photocatalyst to the singlet ground state 5CzBN , the high spin distorted tetrahedral complex T 10 can be reached and undergo reductive elimination through a less energy-demanding pathway of 27.9 kcal/mol. The transition state is described by the incipient formation of a C -O bond between the arene and the carboxylate (dC-O = 1.76 Å) while the nickel atom keeps its distance from the arene ' s carbon (dNi-C = 1.97 Å in T TS5 ‡ vs dNi-C = 1.95 Å in T 10 ). The geometry T 11 corresponds to the establishment of a more solid O -C bond (dC-O = 1.46 Å) with the arene and weakening of the Ni -C bond (dNi-C = 2.07 Å). The energy transfer process from the triplet photoexcited state 5CzBN* to S 10 is an endergonic process ( Δ GEnT = 29.6 kcal/mol), resulting in the decay of the photocatalyst  to 5CzBn and  reaching T 10 ,  in  which  a  reductive

arbara et al.

Scheme 2. Scope of TAQA decarboxylative arylation. Yields determined by 1 H NMR using 1,3,5-trimethoxybenzene as internal standard. a) in parenthesis, isolated yield from reaction with 1 mmol of TAQA.

![Image](Bárbara et al. - 2025 - Protecting group-free photocatalyzed O-arylation of quinic acid_artifacts/image_000007_19d7f8d4c71f61227658f7c950c92d223fbddee33097c58c49da63a49cac0af6.png)

elimination  process  is  more  likely  than  for S 10 .  The  smaller  energy barrier  for  the  reductive  elimination  from  Ni(II),  upon  the  energy transfer process, is analogous to what was previously described for the Ir/Ni  metallaphotoredox  dual  catalysis  [35a].  In  the  present  system, although the origin of the 5CzBN* triplet  state  being  unstudied,  the formation of T 10 is advanced to happen through a MLCT upon energy transfer  from  the  excited  photocatalyst  to  the  Ni(II)  species.  Upon reduction of the nickel complex, and relaxation of the metal complex to the singlet state, the T-distorted S 11 complex keeps a Ni -C interaction (dNi-C = 2.07 Å), while the interaction of the metal with the oxygen atom becomes considerably weaker (dNi-O = 2.12 Å in T 11 vs dNi-O = 2.44 Å in S 11 ).  Release of the ester and taking of another iodo arene molecule releases the nickel(0) η 2 complex 5 to renew the catalytic cycle.

In  order  to  better  understand  the  lack  of  formation  of  decarboxylative arylation products, a mechanism based on literature precedents for  the  nickel-catalyzed  C(sp 3 )-C(sp 2 )  cross-coupling  was  considered. The proposed mechanism follows previous reports on an operative Ni (I)/Ni(III)  catalytic  cycle,  resulting  from  the  Ni(0)/Ni(II)  comproportionation [40] or photocatalytic reduction [40,41] of the Ni(II) initial species.  The energy barriers for the oxidative addition and reductive elimination of N -coordinated Ni(I)/Ni(III) pairs were determined to be a more facile process than a Ni(0)/Ni(II) catalytic cycle [42], suggesting a mechanism based on the catalytic turnover of a Ni(I) species. Thus, a mechanism  starting  from  Ni I Cl · bpy  that  undergoes  addition  to  the radical derived from quinate oxidation and decarboxylation, followed by  oxidative  addition  and  reductive  elimination,  is  presented  as  a reasonable pathway to describe the formation of C-arylated QA derivative (Figs.  S7 -S9 in  Supporting  Information).  The  hypothesized Ni I -Ni II -Ni I -Ni III -Ni I cycle presented involves two SET processes that are  only  slightly  endergonic  (5.5  kcal/mol).  Moreover,  the  most energy-demanding species is only 4.8 kcal/mol higher than the initial pair  or  reactants  considered  and  corresponds  to  the  radical  addition transition state (Fig. S7). The remaining mechanism is characterized by a series of events that are overall highly exergonic ( 88.8 kcal/mol), with the reductive elimination step having the higher energy barrier in the  process  (13.0  kcal/mol)  and  being  the  rate-determining  step  as identified in other studies [31].

In  view  of  the  favorable  process  described  for  the  Ni-catalyzed decarboxylative  arylation,  we  then  looked  into  the  photocatalytic cycle. It is known that the photoredox catalyst 5CzBN undergoes excitation  with  visible  light  to  its  triplet  state  due  to  the  well-separated HOMO  and  LUMO.  Nevertheless,  the Δ EST gap  is  small  enough  to allow reverse intersystem crossing [43]. Carbazolyl cyanobenzene-based photocatalysts generally exhibit strong oxidative and  reductive  capabilities  comparable  to  many  metal  complexes. Nevertheless, the photoredox potentials of 5CzBN show its ability to act as a reducing agent (E1/2(*P/P ) = + 1.20 V; E1/2(P + /*P) = 1.35 V) [44], in opposition to most cyanoarenes known for undergoing reductive quenching of the photoexcited states [45]. With that in mind, the quenching of the photocatalyst through a reductive or oxidative cycle was investigated by DFT (Fig. S5). Comparison of the reductive and oxidative quenching cycles show a strong preference for the latter when comparing  the  energy  barriers  of  single  electron  transfers.  Notwithstanding that the photocatalyst quenching is slightly endergonic ( Δ G = 2.3  kcal/mol),  an  energy  barrier  of  25.1  kcal/mol  still  needs  to  be overcome to promote quinate oxidation. The  comparison of  the  two mechanism pathways considered suggests that the O -arylation path is independent of the photoredox potential of the photocatalyst, as it acts as a photosensitizer to participate in the energy transfer to the nickel intermediate species. Such a conclusion is also supported by the fact that screening of photocatalysts of different redox potentials did not change the reaction outcome.

AG

(kcal/mol)

0.0

«Oxidative addition—

AG (kcal/mol)

7₽

arbara et al.

2.99

. ОН

Fig.  1. Calculated  energetics  for  the  nickel-catalyzed O -arylation  of  QA.  Gibbs  free  energies  were  computed  at  the  BP86(PCM-DMSO)/(SDD,6-31 + G**)  level of theory.

![Image](Bárbara et al. - 2025 - Protecting group-free photocatalyzed O-arylation of quinic acid_artifacts/image_000008_98a402a02fba14f0557bd1aeb930d8290cee11bb7ca191a815c3a2593d764574.png)

Despite the low energetic barriers determined for the O -arylation process, the presence of Ni species in different oxidation states than the one considered here cannot be ruled out due to the recent reports on the ability of the nickel complexes to reach photoexcited states [46].

## 3. Conclusion

In conclusion, a mild, fast, and selective method for the construction of QA-derived esters, surpassing the use of protecting groups was successfully developed. The O -arylation of QA results in the construction of a new O -C(sp 2 ) bond in naturally abundant QA, applicable to a range of aryl  halides,  thus  expanding  the  QA-derived  chemical  space.  The employed  purely  organic  photocatalyst,  and  the  catalytically  active nickel complex is prepared in situ from an inexpensive nickel source. The boundaries  on  the  aryl  halide  scope  have  been  set  to  the  required presence  of  electron-withdrawing  substituents,  but  the  reaction  was nevertheless shown to be amenable to the introduction of heterocyclic moieties.  A  computational  study  on  the  transformation  suggests  an operative Ni catalytic cycle consisting of oxidative addition, anion exchange and reductive elimination, with this last step taking place after energy transfer from the photocatalyst to the Ni(II) species. A comparison  of  the  proposed O -arylation  mechanism  with  a  decarboxylative arylation one indicates that the required single electron transfer processes of the latter are considerably more energy-demanding. This rules out a decarboxylative arylation mechanism despite this being the Nicatalyzed favorable pathway.

## 4. Experimental section

General Information: All solvents were distilled prior to use and, when mentioned, dried over standard drying agents according to the usual  procedures.  Dry  tetrahydrofuran  (THF)  and  dichloromethane (DCM) were obtained from INERT PureSolv micro apparatus. Anhydrous tetrahydrofuran was freshly distilled before use in order to remove the radical  inhibitor  BHT  present  as  a  stabilizer.  DMSO  was  dried  by standing  in  freshly  activated  3  Å  molecular  sieves  (20  %  w/v).  3  Å molecular sieves were supplied by Sigma-Aldrich and activated before use. Activation of the MS was performed in a 400 ◦ C oven for 4 h. DMF was  supplied  by  Aldrich  in  Sureseal ® bottles.  All  the  reagents  were purchased from commercial sources (Sigma-Aldrich, Alfa Aesar, Fluorochem,  Acros,  TCI)  and  used  without  further  purification  unless specified.

All reactions were set up under an argon atmosphere in oven-dried glassware  using  freeze-pump-thaw  cycling.  The  reaction  mixtures, placed in Rotaflo ® stopcock equipped Schlenk tubes of 0.95 cm internal diameter, were irradiated using three Kessil ® PR160L@456 nm lamps, equipped with a cooling fan, according to the set-up picture shown in the supplementary material. The fan speed was set such that the reaction temperature did not go over 30 ◦ C. Reaction mixtures were analyzed by thin  layer  chromatography  (TLC)  using  Merck  silica  gel  60F254 aluminum plates and visualized by UV light and stained with potassium permanganate (KMnO4) or a ceric ammonium molybdate (CAM) stain. Thin-layer chromatography (TLC) was carried out on Kieselgel 60HF254 from Merck Co. and used to monitor the reaction progress. Separation and purification of compounds by flash column chromatography (FCC)

arbara et al.

was performed using Kieselgel 60 (230 -400 mesh, Merck).

1 H and 13 C NMR spectra were acquired on a Bruker MX300 spectrometer. Chemical shifts are reported in ppm from TMS with the solvent resonance as the internal standard (CHCl3: δ = 7.27 ppm for 1 H NMR and δ = 77.0 ppm for 13 C NMR). Data are reported as follows: chemical shift ( δ ), multiplicity (s = singlet, d = doublet, t = triplet, q = quartet, dd = doublet of doublets, m = multiplet), coupling constants (Hz). 19 F NMR spectra were acquired on a Bruker Fourier 400. The Liquid chromatography -mass spectrometry (LC-MS) was realized using a Dionex Ultimate 3000 UHPLC + system equipped with a Multiple-Wavelength detector, using  an  imChem  Surf  C18  TriF  100A  3 μ m 100 × 2.1  mm column coupled to a Thermo Scientific Q Exactive hybrid quadrupole-Orbitrap mass  spectrometer  (Thermo  ScientificTM  Q  ExactiveTM  Plus).  The mass spectrometry was performed using the previously mentioned mass spectrometer. Tetraacetyl quinic acid (TAQA) was prepared from quinic acid  according  to  a  previously  reported  procedure  [47].  The  photocatalysts were obtained according to previously established procedures, namely 3DPAFIPN, 4CZIPN, and 5CZIPN, with similar spectral characterization as there described [45c].

General Procedure for quinic acid (QA) O -arylation: A flame-dried 10  mL  Schlenk  tube,  equipped  with  a  Rotaflo ® stopcock,  magnetic stirring bar and an argon supply tube, was firstly charged under argon with NiCl2.Glyme (2.2 mg, 0.01 mmol, 5 mol%). The tube was then charged  with  4-iodobenzotrifluoride  (54.4  mg,  0.2  mmol,  1  equiv.), 5CzBN (1.9 mg, 0.002 mmol, 1 mol%), 2,2 ′ -bipyridine (bpy, 1.64 mg, 0.0105 mmol, 5.25 mol%), phthalimide (29.4 mg, 0.2 mmol, 1 equiv.) and QA (76.9 mg, 0.4 mmol, 2 eq) followed by anhydrous DMSO (2 mL). The reaction mixture was vigorously stirred for 5 min, after which 2tert -butyl-1,1,3,3-tetramethylguanidine (btmg, 68.4 mg, 0.4 mmol, 2 equiv.) was added, the reaction mixture was further subjected to a freeze-pumpthaw procedure (three cycles), and the vessel was refilled with argon. The reaction mixture was irradiated under vigorous stirring for 15 h. After that, the reaction mixture was quenched with water (approx. 2 mL) and extracted with EtOAc (4 × 3 mL). Trimethoxybenzene (tmb, 1.2 mg, 0.067 mmol) was added as an internal standard, and the solvent was removed under reduced pressure, followed by dissolution in DMSO -d 6 and 1 H NMR analysis to determine the reaction yield.

4-trifluoromethylphenyl quinate (2): 1 H NMR (300 MHz, DMSO -d 6) δ 7.62 (d, J = 8.6 Hz, 2H), 6.99 (d, J = 8.5 Hz, 2H), 4.61 (s, 1H), 4.26 (s, 1H), 4.04 -3.94 (m, 1H), 3.85 (td, J = 6.3, 3.5 Hz, 1H), 3.42 (dd, J = 6.2, 3.0 Hz, 1H), 2.29 -1.91 (m, 4H);  13 C{1H} NMR (75 MHz, DMSO -d 6) δ 177.5, 158.1, 135.6, 127.7, 127.6, 124.3, 118.8, 83.2, 75.1, 69.6, 66.8, 38.7, 35.7; ESI-HRMS calcd for C14H15F3O6 [M + H] + , 337.2707; found 337.0886.

General Procedure for tetraacetate quinic acid ( TAQA) O -arylation : A  flame-dried  10  mL  Schlenk  tube,  equipped  with  a  Rotaflo ® stopcock, magnetic stirring bar and an argon supply tube, was firstly charged under argon with NiCl2 (1.3 mg, 0.01 mmol, 5 mol%). The tube was  then  charged  with  the  aryl  halide  (0.2  mmol),  5CzBN  (1.9  mg, 0.002 mmol, 1 mol%), 4,4 ′ -ditert -butyl-2,2 ′ -dipyridyl (dtbbpy, 2.81 mg, 0.0105 mmol, 5.25 mol%.), phthalimide (29.4 mg, 0.2 mmol, 1 equiv.) and TAQA ( 3 )  (144  mg, 0.4 mmol, 2 equiv.) followed by anhydrous DMSO (2 mL). The reaction mixture was vigorously stirred for 5 min, after which btmg (68.4 mg, 0.4 mmol, 2 equiv.) was added, the reaction mixture was further subjected to a freeze-pump-thaw procedure (three cycles), and the vessel was refilled with argon. The reaction mixture was irradiated  under  vigorous  stirring  for  15  h.  After  that,  the  reaction mixture was quenched with water (approx. 2 mL) and extracted with EtOAc (4 × 3 mL). The combined organic layers were washed (3 × 1 mL) with an aqueous solution of NaOH (1 % w/v), washed with brine, dried over anhydrous Mg2SO4 and filtered. Trimethoxybenzene (tmb, 1.2 mg, 0.067 mmol) was added as an internal standard, and the solvent was removed under reduced pressure, followed by dissolution in CDCl3 and 1 H NMR analysis to determine the reaction yield. The mixture was purified by flash column chromatography or preparative TLC (PTLC) using n -Hexane:EtOAc (30 -50 % of EtOAc) as eluent to obtain pure compound for spectral characterization.

4-trifluoromethylphenyl  tetraacetyl  quinate ( 4a ) : Obtained  in  45  % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane), 1 H NMR (300 MHz, CDCl3) δ 7.64 (d, J = 8.6 Hz, 2H), 7.20 (d, J = 9.0, 2H), 5.62 (q, J = 3.8 Hz, 1H), 5.48 (td, J = 9.8, 4.3 Hz, 1H), 5.07 (dd, J = 9.5, 3.5 Hz, 1H), 2.77 -2.64 (m, 2H), 2.50 (dd, J = 15.7, 3.8 Hz, 1H), 2.17 (s, 3H), 2.09 (s, 3H), 2.04 (s, 3H), 2.03 (s, 3H); 13 C{1H}  NMR  (75  MHz,  CDCl3) δ 170.1,  170.1,  170.0,  169.9, 168.9, 153.0, 129.4, 129.3, 129.0, 128.5, 127.1, 127.1, 127.02, 126.97, 125.7, 122.1, 121.8, 78.6, 77.4, 71.3, 67.5, 66.6, 36.5, 32.2, 29.8, 21.07, 21.05, 21.03, 20.8; 19 F{1H} NMR (376 MHz, CDCl3) δ 62.3; ESI-HRMS calcd for C22H27F3O10N [M + NH4] + : 522.1582, found 522.1570.

3,5-di(trifluoromethyl)phenyl tetraacetyl quinate ( 4b ) : Obtained in 54 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane). 1 H NMR (300 MHz, CDCl3) δ 7.77 (s, 1H), 7.54 (s, 2H), 5.62 (q, J = 3.9 Hz, 1H), 5.47 (td, J = 9.7, 4.3 Hz, 1H), 5.08 (dd, J = 9.4, 3.5 Hz, 1H), 2.77 -2.62 (m, 2H), 2.51 (dd, J = 15.5, 3.7 Hz, 1H), 2.19 (s, 3H), 2.09 (s, 3H), 2.04 (s, 3H), 2.03 (s, 3H); 13 C{1H} NMR (75 MHz, CDCl3) δ 170.3, 170.1, 170.0, 169.9, 168.7, 150.9, 133.9, 133.4, 133.0, 132.5, 128.1, 124.5, 122.3, 120.9, 120.3, 117.3, 78.4, 71.0, 67.3, 66.5, 36.4, 32.1, 29.8, 21.1, 21.0, 20.8; 19 F{1H} NMR (376 MHz, CDCl3) δ 62.9; ESI-HRMS calcd for C23H26F6O10N [M + NH4] + : 590.1455; found 590.1443.

4-cyanophenyl tetraacetyl quinate ( 4c ) : Obtained in 62 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane). 1 H NMR (300 MHz, CDCl3) δ 7.69 (d, J = 9.0, 2H), 7.21 (d, J = 9.0, 2H), 5.61 (q, J = 3.9 Hz, 1H), 5.46 (td, J = 9.8, 4.3 Hz, 1H), 5.06 (dd, J = 9.4, 3.5 Hz, 1H), 2.77 -2.60 (m, 2H), 2.48 (dd, J = 15.7, 3.8 Hz, 1H), 2.17 (s, 3H), 2.09 (s, 3H), 2.03 (s, 3H), 2.02 (s, 3H); 13 C{1H} NMR (75 MHz, CDCl3) δ 170.2, 170.1, 170.0, 169.9, 168.6, 153.6, 133.9, 122.5, 118.2, 110.4, 78.5, 71.1, 67.4, 66.5, 36.4, 32.1, 21.1,  21.0,  20.8;  ESI-HRMS  calcd  for  C22H27N2O10  [M + NH4] + : 479.1660; found 479.1651.

4-acetylphenyl tetraacetyl quinate ( 4d ) : Obtained in 44 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane). 1 H NMR (300 MHz, CDCl3) δ 7.97 (d, J = 9.0, 2H), 7.15 (d, J = 9.0, 2H), 5.61 (q, J = 3.6 Hz, 1H), 5.47 (td, J = 9.9, 4.3 Hz, 1H), 5.06 (dd, J = 9.5, 3.5 Hz, 1H), 2.78 -2.61 (m, 2H), 2.58 (s, 3H), 2.49 (dd, J = 15.8, 3.6 Hz, 1H), 2.17 (s, 3H), 2.09 (s, 3H), 2.03 (s, 3H), 2.02 (s, 3H); 13 C{1H} NMR (75 MHz, CDCl3) δ 196.9, 170.13, 170.11, 170.0, 169.9, 168.8, 154.0, 135.2, 130.1, 121.5, 78.6, 71.2, 67.4, 66.5, 36.5, 32.1,  29.6,  26.8,  21.09,  21.07,  21.04,  20.8;  ESI-HRMS  calcd  for C23H30O11N [M + NH4] + : 496.1813; found 496.1812.

9 H -fluoren2-yl tetraacetyl quinate ( 4e ) : Obtained in 60 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane); 1 H NMR (300 MHz, CDCl3) δ 7.74 (d, J = 8.0 Hz, 2H), 7.52 (d, 1H), 7.41 -7.26 (m, 3H), 7.06 (dd, J = 8.3, 2.2 Hz, 1H), 5.64 (q, J = 3.7 Hz, 1H), 5.52 (td, J = 10.0, 4.3 Hz, 1H), 5.09 (dd, J = 9.6, 3.5 Hz, 1H), 3.88 (s, 2H), 2.82 -2.71 (m, 2H), 2.53 (dd, J = 15.9, 3.7 Hz, 1H), 2.20 (s, 3H), 2.11 (s, 3H), 2.05 (s, 6H); 13 C{1H} NMR (75 MHz, CDCl3) δ 170.09,  170.06,  170.0,  169.9,  169.4,  149.5,  144.8,  143.4, 140.9, 140.1, 127.0, 126.9, 125.1, 120.5, 120.0, 119.7, 118.2, 78.8, 71.5,  67.7,  66.6,  37.0,  36.7,  32.2,  21.2,  21.1,  21.0,  20.8;  ESI-HRMS calcd for C28H32O10N [M + NH4] + 542.2021; found 542.2014.

3-carbomethoxyphenyl tetraacetyl quinate ( 4f ) : Obtained in 52 % according to 1H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane); 1 H NMR (300 MHz, CDCl3) δ 7.93 (dt, J = 7.8,  1.3  Hz,  1H),  7.74 -7.70  (m,  1H),  7.46  (t, J = 7.9  Hz,  1H), 7.31 -7.27 (m, 1H), 5.63 (q, J = 3.8 Hz, 1H), 5.50 (td, 1H), 5.08 (dd, J = 9.5, 3.5 Hz, 1H), 3.92 (s, 3H), 2.79 -2.67 (m, 2H), 2.51 (dd, J = 15.7, 3.7 Hz, 1H), 2.18 (s, 3H), 2.10 (s, 3H), 2.05 (s, 3H), 2.04 (s, 3H). 13 C NMR (75 MHz, CDCl3) δ 170.11, 170.07, 170.04, 169.88, 169.07, 166.12, 150.44, 131.98, 129.74, 127.65, 126.02, 122.44, 78.67, 77.36, 71.37, 67.59, 66.61, 52.50, 36.65, 32.19, 29.84, 21.12, 21.07, 20.83, 1.16; ESIHRMS calcd for C23H30O12N [M + NH4] + , 512.1763; found 512.1760.

2-cyanophenyl tetraacetyl quinate ( 4g ) : Obtained in 50 % according to

arbara et al.

1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane); 1 H NMR (300 MHz, CDCl3) δ 7.72 -7.57 (m, 2H), 7.42 -7.23 (m, 2H), 5.62 (q, J = 3.6 Hz, 1H), 5.54 (td, J = 10.3, 4.4 Hz, 1H), 5.05 (dd, J = 9.9, 3.6 Hz, 1H), 2.88 -2.69 (m, 2H), 2.50 (dd, J = 16.0, 3.6 Hz, 1H), 2.21 (s, 3H), 2.10 (s, 3H), 2.05 (s, 3H), 2.02 (s, 3H); 13 C{1H} NMR (75 MHz, CDCl3) δ 170.4, 170.13, 170.07, 169.9, 168.2, 151.7, 134.4, 133.4, 126.9, 123.1, 114.9, 106.8, 78.6, 71.6, 67.5, 66.1, 36.6, 32.2, 21.13, 21.10, 20.8; ESI-HRMS calcd for C22H27N2O10 [M + NH4] + : 479.1660; found 479.1649.

4-( α -bromoacetyl)phenyl tetraacetyl quinate ( 4h ) : Obtained in 62 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane); 1 H NMR (300 MHz, CDCl3) δ 7.71 (d, J = 9.0, 2H), 7.62 (d, J = 9.0, 2H), 5.57 (q, J = 3.4 Hz, 1H), 5.49 (td, 1H), 5.30 (dd, 2H), 5.01 (dd, J = 10.2, 3.6 Hz, 1H), 2.81 -2.68 (m, 2H), 2.43 (dd, J = 16.1, 3.5 Hz, 1H), 2.13 (s, 4H), 2.06 (s, 3H), 2.04 (s, 3H), 1.99 (s, 3H); 13 C{1H}  NMR  (75  MHz,  CDCl3) δ 190.6,  170.2,  170.1,  170.0, 169.9, 169.7, 132.6, 132.4, 129.5, 129.3, 78.8, 72.0, 67.7, 66.5, 66.3, 37.2, 31.9, 29.6, 21.2, 21.1, 20.8; ESI-HRMS calcd for C23H29BrO11N [M + NH4] + : 574.09161; found 574.0916.

1-naphthyl tetraacetyl quinate ( 4i ) : Obtained in 68 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane); 1 H NMR (300 MHz, CDCl3) δ 7.92 -7.79 (m, 2H), 7.74 (d, J = 1.0 Hz, 1H), 7.59 -7.44 (m, 2H), 7.44 (t, J = 7.9 Hz, 1H), 7.21 (dd, J = 7.6, 1.1 Hz, 1H), 5.66 (q, J = 3.7 Hz, 1H), 5.58 (td, J = 10.2, 4.4 Hz, 1H), 5.11 (dd, J = 9.8, 3.6 Hz, 1H), 2.99 -2.85 (m, 1H), 2.59 (dd, J = 15.9, 3.6 Hz, 1H), 2.22 (s, 4H), 2.12 (s, 3H), 2.05 (s, 6H); 13 C{1H} NMR (75 MHz, CDCl3) δ 170.18, 170.15, 170.1, 169.9, 169.2, 146.3, 134.7, 128.1, 126.8, 126.7, 126.5, 125.4, 120.9, 117.7, 79.0, 71.6, 67.6, 66.4, 37.0, 36.2, 32.2, 29.57, 21.2, 21.1, 20.8; ESI-HRMS  calcd for C25H30O10N [M + NH4] + : 504.1864; found 504.1862.

9-phenanthrenyl tetraacetyl quinate ( 4j ) : Obtained in 68 % according to 1 H NMR analysis, isolated as a pale oil after purification by PTLC (40 % EtOAC in Hexane); 1 H NMR (300 MHz, CDCl3) δ 8.70 (d, J = 7.0, 1H), 8.65 (d, J = 8.4, 1H), 7.93 (dd, J = 7.9, 1.7 Hz, 1H), 7.85 (dd, J = 7.3, 2.0 Hz, 1H), 7.75 -7.56 (m, 4H), 7.50 (s, 1H), 5.69 (q, J = 3.7 Hz, 1H), 5.61 (td, J = 10.3, 4.4 Hz, 1H), 5.14 (dd, J = 9.8, 3.5 Hz, 1H), 2.95 (m, J = 13.7, 7.2, 3.2 Hz, 2H), 2.64 (dd, J = 15.9, 3.6 Hz, 1H), 2.25 (s, 4H), 2.14  (s,  3H),  2.06  (s,  6H); 13 C{1H} NMR (75 MHz, CDCl3) δ 170.3, 170.2, 170.1, 170.0, 169.3, 144.7, 131.6, 131.4, 129.1, 128.7, 127.5, 127.3, 127.2, 126.8, 126.3, 123.1, 122.8, 121.7, 117.5, 79.1, 71.7, 67.7, 66.5, 37.1, 32.3, 21.3, 21.1, 20.9; ESI-HRMS calcd for C25H30O10N [M + NH4] + : 554.2021; found 554.2015.

## CRediT authorship contribution statement

Miguel  A.  B ´ arbara: Writing -review &amp; editing,  Methodology, Investigation. Nuno R. Candeias: Writing -review &amp; editing, Writing -original draft, Supervision, Project administration, Investigation, Conceptualization. Luis F. Veiros: Writing -review &amp; editing, Supervision. Filipe  Menezes: Writing -review &amp; editing,  Data  curation. Andrea Gualandi: Writing -review &amp; editing,  Supervision. Pier  G. Cozzi: Writing -review &amp; editing, Supervision. Carlos A.M. Afonso: Writing -review &amp; editing, Supervision.

## Declaration of competing interest

The  authors  declare  the  following  financial  interests/personal  relationships which may be considered as potential competing interests: Nuno Filipe Rafael Candeias reports financial support was provided by Foundation for Science and Technology. Carlos A. M. Afonso reports financial  support  was provided by Foundation for Science and Technology. Luis F. Veiros reports financial support was provided by Foundation  for  Science  and  Technology.  If  there  are  other  authors,  they declare that they have no known competing financial interests or personal relationships that could have appeared to influence the work reported in this paper.

## Acknowledgments

This work received financial support from PT national funds (FCT/ MCTES, Fundaç ˜ ao para a Ci ˆ encia e Tecnologia and Minist ´ erio da Ci ˆ encia,  Tecnologia  e  Ensino  Superior)  through  the  projects  PTDC/QUIQOR/1131/2020, UIDB/04138/2020 | UIDP/04138/2020 and UIDB/ 50006/2020 | UIDP/50006/2020. NRC further acknowledges FCT for financial  support  under  the  Scientific  Employment  Stimulus  (CEECINST/2018). CSC -IT Center for Science Ltd, Finland is acknowledged for  the  allocation  of  computational  resources.  Centro  de  Química Estrutural  (CQE)  and  Institute  of  Molecular  Sciences  (IMS)  also acknowledge  the  financial  support  of  FCT/MCTES  (Projects  UIDB/ 00100/2020, UIDP/00100/2020, and LA/P/0056/2020, respectively).

## Appendix A. Supplementary data

Supplementary data to this article can be found online at https://doi. org/10.1016/j.tgchem.2025.100070.

## Data availability

Data will be made available on request.

## References

- [1] [S. Parmaki, F.C. Ferreira, T. Esteves, C.A.M. Afonso, M. Koutinas, in: S. Varjani, A. Pandey, T. Bhaskar, S.V. Mohan, D.C.W. Tsang (Eds.), Biomass, Biofuels, Biochemicals: Circular Bioeconomy: Technologies for Biofuels and Biochemicals, Elsevier Inc. All, 2022, pp. 295 -335.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref1)
- [2] a) F. Allais, Curr. Opin. Green Sustainable Chem. 40 (2023); b) H. Zhu, Y. Cai, S. Ma, Y. Futamura, J. Li, W. Zhong, X. Zhang, H. Osada, H. Zou, ChemSusChem 14 (2021) 5320 -5327.
- [3] [A. Antenucci, S. Dughera, P. Renzi, ChemSusChem 14 (2021) 2785 -2853.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref2)
- [4] a) R.A. Fernandes, P. Kumar, P. Choudhary, Eur. J. Org. Chem. 2021 (2020) 711 -740;
- b) R.A. Fernandes, P. Kumar, P. Choudhary, Chem. Commun. 56 (2020) 8569 -8590.
- [5] A. Gogoi, Mezhubeinuo, S. Nongrum, G. Bez, Curr. Org. Chem. 25 (2021) 1566 -1610.
- [6] a) D. Wianowska, M. Gil, Phytochem. Rev. 18 (2018) 273 -302; b) M.N. Clifford, I.B. Jaganath, I.A. Ludwig, A. Crozier, Nat. Prod. Rep. 34 (2017) 1391 -1421;
- c) [N. Yuda, M. Tanaka, M. Suzuki, Y. Asano, H. Ochi, K. Iwatsuki, J. Food. Sci. 77 (2012) H254 -H261;](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib6c)
- d) [L. Grunovait ˙ e, M. Pukalskien ˙ e, A. Pukalskas, P.R. Venskutonis, J. Funct.Foods 24 (2016) 85 -96;](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib6d)
- e) J. Wang, D. Lu, Q. Sun, H. Zhao, X. Ling, P. Ouyang, Chem. Eng. Sci. 78 (2012) 53 -62;
- f) [O. Santana-M ´ eridas, A. Gonz ´ alez-Coloma, R. S ´ anchez-Vioque, Phytochem. Rev. 11 (2012) 447 -466;](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib6f)
- g) [M.I. Sifola, L. Carrino, E. Cozzolino, L. del Piano, G. Graziani, A. Ritieni, Sustainability 13 (2021) 2087.](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib6g)
- [7] [E. Arceo, J.A. Ellman, R.G. Bergman, ChemSusChem 3 (2010) 811 -813.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref4)
- [8] a) B. Assoah, L.F. Veiros, C.A.M. Afonso, N.R. Candeias, Eur. J. Org. Chem. (2016) 3856 -3861;
- b) N. Ran, D.R. Knop, K.M. Draths, J.W. Frost, J. Am. Chem. Soc. 123 (2001) 10927 -10934.
- [9] [T. Pfennig, J.M. Carraher, A. Chemburkar, R.L. Johnson, A.T. Anderson, J.P. Tessonnier, M. Neurock, B.H. Shanks, Green Chem. 19 (2017) 4879 -4888.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref5)
- [10] a) S. Wu, W. Chen, S. Lu, H. Zhang, L. Yin, Molecules 27 (2022) 4779; b) E. Valanciene, N. Malys, Antioxidants 11 (2022) 2427;
- c) [N.R. Candeias, B. Assoah, S.P. Simeonov, Chem. Rev. 118 (2018) 10458 -10550.](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib10c)
- [11] a) V. Enev, in: E.M. Carreira, H. Yamamoto (Eds.), Comprehensive Chirality, Elsevier, Amsterdam, 2012, pp. 325 -345; b) A. Barco, S. Benetti, C.D. Risi, P. Marchetti, G.P. Pollini, V. Zanirato, Tetrahedron Asymmetry 8 (1997) 3515 -3545;
- c) [J. Mulzer, M. Drescher, V.S. Enev, Curr. Org. Chem. 12 (2008) 1613 -1630.](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib11c)
- [12] [R. Altmann, H. Falk, Monatsh. Chem. 126 (1995) 1225 -1232.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref6)
- [13] [D.H. Hua, K. Lou, J. Havens, E.M. Perchellet, Y. Wang, J.-P. Perchellet, T. Iwamoto, Tetrahedron 60 (2004) 10155 -10163.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref7)
- [14] [D. Magdziak, G. Lalic, H.M. Lee, K.C. Fortner, A.D. Aloise, M.D. Shair, J. Am. Chem. Soc. 127 (2005) 7284 -7285.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref8)
- [15] [M. Frank, R. Miethchen, Carbohydr. Res. 313 (1998) 49 -53.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref9)
- [16] a) N.A. Romero, D.A. Nicewicz, Chem. Rev. 116 (2016) 10075 -10166; b) A.Y. Chan, I.B. Perry, N.B. Bissonnette, B.F. Buksh, G.A. Edwards, L.I. Frye, O. L. Garry, M.N. Lavagnino, B.X. Li, Y. Liang, E. Mao, A. Millet, J.V. Oakley, N. L. Reed, H.A. Sakai, C.P. Seath, D.W.C. MacMillan, Chem. Rev. 122 (2022) 1485 -1542;

arbara et al.

c) N.E.S. Tay, D. Lehnherr, T. Rovis, Chem. Rev. 122 (2022) 2487 -2649; d) M.J.P. Mandigma, J. Kaur, J.P. Barham, ChemCatChem 15 (2023); e) S.B. Beil, S. Bonnet, C. Casadevall, R.J. Detz, F. Eisenreich, S.D. Glover, C. Kerzig, L. Naesborg, S. Pullen, G. Storch, N. Wei, C. Zeymer, JACS Au 4 (2024) 2746 -2766.

- [17] [G.E.M. Crisenza, P. Melchiorre, Nat. Commun. 11 (2020) 803.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref10)
- [18] a) M. Cortes-Clerget, J. Yu, J.R.A. Kincaid, P. Walde, F. Gallou, B.H. Lipshutz, Chem. Sci. 12 (2021) 4237 -4266; b) V. Perchyonok, I. Lykakis, Curr. Org. Chem. 13 (2009) 573 -598.
- [19] a) Z. Huang, N. Luo, C. Zhang, F. Wang, Nat. Rev. Chem 6 (2022) 197 -214; b) T. Liu, J. Huang, J. Li, K. Wang, Z. Guo, H. Wu, S. Yang, H. Li, Green Chem. 25 (2023) 10338 -10365; c) D. Aboagye, R. Djellabi, F. Medina, S. Contreras, Angew. Chem. Int. Ed. Engl. 62 (2023) e202301909.
- [20] [J. Schwarz, B. K ¨ onig, Green Chem. 20 (2018) 323 -361.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref11)
- [21] [J. Guo, D. Norris, A. Ramirez, J.L. Sloane, E.M. Simmons, J.M. Ganley, M. S. Oderinde, T.G.M. Dhar, G.H.M. Davies, T.C. Sherwood, ACS Catal. (2023) 11910 -11918.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref12)
- [22] [C.N. Prieto Kullmer, J.A. Kautzky, S.W. Krska, T. Nowak, S.D. Dreher, D.W. C. MacMillan, Science 376 (2022) 532 -539.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref13)
- [23] [E.R. Welin, C. Le, D.M. Arias-Rotondo, J.K. McCusker, D.W. MacMillan, Science 355 (2017) 380 -385.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref14)
- [24] [C.D. McTiernan, X. Leblanc, J.C. Scaiano, ACS Catal. 8 (2018) 9847, 9847.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref15)
- [25] C. Fischer, C. Kerzig, B. Zilate, O.S. Wenger, C. Sparr, ACS Catal. 10 (2019) 210 -215.
- [26] [R. Sun, Y. Qin, D.G. Nocera, Angew. Chem. Int. Ed. Engl. 59 (2020) 9527 -9533.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref17)
- [27] [Z. Zuo, D.T. Ahneman, L. Chu, J.A. Terrett, A.G. Doyle, D.W. MacMillan, Science 345 (2014) 437 -440.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref18)
- [28] [J. Lu, B. Pattengale, Q. Liu, S. Yang, W. Shi, S. Li, J. Huang, J. Zhang, J. Am. Chem. Soc. 140 (2018) 13719 -13725.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref19)
- [29] [E. Pinosa, E. Bassan, S. Cetin, M. Villa, S. Potenti, F. Calogero, A. Gualandi, A. Fermi, P. Ceroni, P.G. Cozzi, J. Org. Chem. 88 (2023) 6390 -6400.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref20)
- [30] [M. Pitchai, A. Ramirez, D.M. Mayder, S. Ulaganathan, H. Kumar, D. Aulakh, A. Gupta, A. Mathur, J. Kempson, N. Meanwell, Z.M. Hudson, M.S. Oderinde, ACS Catal. 13 (2023) 647 -658.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref21)
- [31] [K. Kolahdouzan, R. Khalaf, J.M. Grandner, Y. Chen, J.A. Terrett, M.P. Huestis, ACS Catal. 10 (2019) 405 -411.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref22)
- [32] a) D.A. Cagan, D. Bim, N.P. Kazmierczak, R.G. Hadt, ACS Catal. 14 (2024) 9055 -9076;
- b) [S.I. Ting, W.L. Williams, A.G. Doyle, J. Am. Chem. Soc. 144 (2022) 5575 -5582.](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib32b)
- [33] [H. Huang, J.L. Alvarez-Hernandez, N. Hazari, B.Q. Mercado, M.R. Uehling, ACS Catal. 14 (2024) 6897 -6914.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref23)
- [34] [H. Beckurts, P. Echtermeier, Arch. Pharmazie 244 (1906) 37 -57.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref24)
- [35] a) Y.J. Dong, Z.W. Zhao, Y. Geng, Z.M. Su, B. Zhu, W. Guan, Inorg. Chem. 62 (2023) 1156 -1164; b) R.D. Bradley, B.D. McManus, J.G. Yam, V. Carta, A. Bahamonde, Angew. Chem. Int. Ed. Engl. 62 (2023) e202310753; c) L. Tian, N.A. Till, B. Kudisch, D.W.C. MacMillan, G.D. Scholes, J. Am. Chem. Soc. 142 (2020) 4555 -4559;

[d)](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib35d)

[P. Ma, S. Wang, H. Chen, ACS Catal. 10 (2019) 1](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib35d)

[-](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib35d)

[6.](http://refhub.elsevier.com/S2773-2231(25)00009-3/bib35d)

- [36] a) J.N. Harvey, Annu. Rep. Prog. Chem., Sect. C 102 (2006) 203 -226; b) J.N. Harvey, in: N. Kaltsoyannis, J.E. McGrady (Eds.), Principles and Applications of Density Functional Theory in Inorganic Chemistry I, Springer Berlin Heidelberg, Berlin, Heidelberg, 2004, pp. 151 -184.
- [37] M. Yuan, Z. Song, S.O. Badir, G.A. Molander, O. Gutierrez, J. Am. Chem. Soc. 142 (2020) 7225 -7234.
- [38] [B. Maity, C. Zhu, H. Yue, L. Huang, M. Harb, Y. Minenkov, M. Rueping, L. Cavallo, J. Am. Chem. Soc. 142 (2020) 16942 -16952.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref26)
- [39] [J.B. Diccianni, T. Diao, Trends Chem. 1 (2019) 830 -844.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref27)
- [40] N.A. Till, S. Oh, D.W.C. MacMillan, M.J. Bird, J. Am. Chem. Soc. 143 (2021) 9332 -9337.
- [41] a) N.A. Till, L. Tian, Z. Dong, G.D. Scholes, D.W.C. MacMillan, J. Am. Chem. Soc. 142 (2020) 15830 -15841; b) H. Ren, G.-F. Li, B. Zhu, X.-D. Lv, L.-S. Yao, X.-L. Wang, Z.-M. Su, W. Guan, ACS Catal. 9 (2019) 3858 -3865.
- [42] [I. Kalvet, Q. Guo, G.J. Tizzard, F. Schoenebeck, ACS Catal. 7 (2017) 2126 -2132.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref29)
- [43] [S. Tanimoto, T. Suzuki, H. Nakanotani, C. Adachi, Chem. Lett. 45 (2016) 770 -772.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref30)
- [44] a) W. Ou, R. Zou, M. Han, L. Yu, C. Su, Chin. Chem. Lett. 31 (2020) 1899 -1902; b) T. Gandini, L. Dolcini, L. Di Leo, M. Fornara, A. Bossi, M. Penconi, A. Dal Corso, C. Gennari, L. Pignataro, ChemCatChem 14 (2022) e202200990.
- [45] a) A. Tlili, S. Lakhdar, Angew. Chem. Int. Ed. Engl. 60 (2021) 19526 -19549; b) Y. Kwon, J. Lee, Y. Noh, D. Kim, Y. Lee, C. Yu, J.C. Roldao, S. Feng, J. Gierschner, R. Wannemacher, M.S. Kwon, Nat. Commun. 14 (2023) 92; c) E. Speckmeier, T.G. Fischer, K. Zeitler, J. Am. Chem. Soc. 140 (2018) 15353 -15365.
- [46] a) S.I. Ting, S. Garakyaraghi, C.M. Taliaferro, B.J. Shields, G.D. Scholes, F. N. Castellano, A.G. Doyle, J. Am. Chem. Soc. 142 (2020) 5800 -5810; b) B.J. Shields, B. Kudisch, G.D. Scholes, A.G. Doyle, J. Am. Chem. Soc. 140 (2018) 3035 -3039.
- [47] [C. Grandjean, C. Rommens, H. Gras-Masse, O. Melnyk, J. Chem. Soc. Perkin Trans. 1 (1999) 2967 -2975.](http://refhub.elsevier.com/S2773-2231(25)00009-3/sref31)