import unittest

from google_scholar import Block, Span, segment_blocks


def _block(text: str) -> Block:
    return Block(kind="para", spans=[Span(text=text)])


class JoinedSeparateOpinionTests(unittest.TestCase):
    def test_cohen_blackmun_joinder_byline_starts_dissent(self):
        parts = segment_blocks([
            _block("MR. JUSTICE HARLAN delivered the opinion of the Court."),
            _block("The judgment below is reversed."),
            _block(
                "MR. JUSTICE BLACKMUN, with whom THE CHIEF JUSTICE and "
                "MR. JUSTICE BLACK join."
            ),
            _block("I dissent, and I do so for two reasons:"),
            _block("Cohen's antic, in my view, was mainly conduct."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "dissent"])
        self.assertIn("BLACKMUN", parts[1].label)
        self.assertTrue(parts[1].blocks[0].text().startswith("MR. JUSTICE BLACKMUN"))

    def test_the_older_parenthesized_joinder_heading(self):
        # The China, 74 U.S. (7 Wall.) 53 (1868): Clifford's concurrence,
        # headed "Mr. Justice CLIFFORD (with whom concurred Mr. Justice
        # FIELD):", ran on as the Court's opinion.  Its own first words say
        # which way it goes.
        parts = segment_blocks([
            _block("Mr. Justice SWAYNE delivered the opinion of the court."),
            _block("This is a case arising out of a collision between the "
                   "steamship China and the brig Kentucky."),
            _block("We have been impressed with the importance of its right "
                   "administration in this case."),
            _block("Mr. Justice CLIFFORD (with whom concurred Mr. Justice "
                   "FIELD):"),
            _block("I concur in the proposition that the pilot laws of New "
                   "York afford no defence to the appellants in this case."),
            _block("DECREE AFFIRMED."),
        ])
        self.assertEqual([part.kind for part in parts],
                         ["majority", "concurrence"])
        self.assertIn("CLIFFORD", parts[1].label)

    def test_a_vote_with_whom_others_concurred_is_no_heading(self):
        # The Scotland, 118 U.S. 507 (1886): the dissenters' vote, set as a
        # sentence, heads no writing of theirs.
        parts = segment_blocks([
            _block("Mr. Justice BRADLEY delivered the opinion of the court."),
            _block("The decree is reversed."),
            _block("Mr. JUSTICE MATTHEWS, with whom concurred Mr. JUSTICE "
                   "MILLER, Mr. JUSTICE HARLAN, and Mr. JUSTICE GRAY "
                   "dissented."),
        ])
        self.assertEqual([part.kind for part in parts], ["majority"])

    def test_judges_joining_after_the_role_still_open_the_opinion(self):
        # Jansen v. Packaging Corp. of Am., 123 F.3d 490 (7th Cir. 1997)
        # (en banc): Flaum's concurrence names its joiners after the role,
        # with the docket each joins in, and ran on as the per curiam's.
        parts = segment_blocks([
            _block("PER CURIAM."),
            _block("The judgments are affirmed."),
            _block(
                "FLAUM, Circuit Judge, concurring, joined by CUMMINGS, BAUER "
                "(in No. 96-1361), CUDAHY (in 95-3128), KANNE (in part), and "
                "EVANS, Circuit Judges."
            ),
            _block("Title VII makes it unlawful for any employer to "
                   "discriminate."),
            _block("The question is when an employer is liable."),
            _block("I would answer it so."),
            _block("CUDAHY, Circuit Judge, concurring."),
            _block("I participated in this en banc rehearing."),
        ])

        self.assertEqual([part.kind for part in parts],
                         ["majority", "concurrence", "concurrence"])
        self.assertTrue(parts[1].label.startswith("FLAUM"))

    def test_a_sentence_after_the_role_opens_nothing(self):
        parts = segment_blocks([
            _block("PER CURIAM."),
            _block("The judgments are affirmed."),
            _block(
                "FLAUM, Circuit Judge, concurring, joined the court's "
                "opinion. He wrote separately to say more."
            ),
        ])

        self.assertEqual([part.kind for part in parts], ["majority"])

    def test_a_sentence_naming_a_justice_s_dissent_opens_nothing(self):
        # United States v. Leon, 468 U.S. 897 (1984): a sentence of
        # Brennan's dissent had opened a phantom "In Brinegar" dissent.
        parts = segment_blocks([
            _block("JUSTICE WHITE delivered the opinion of the Court."),
            _block("The judgment is reversed."),
            _block("JUSTICE BRENNAN, with whom JUSTICE MARSHALL joins, "
                   "dissenting."),
            _block("The exclusionary rule is part of the Amendment."),
            _block(
                "In Brinegar, Justice Jackson, after observing that "
                "\u201c[i]ndications are not wanting that Fourth Amendment "
                "freedoms are tacitly marked as secondary rights,\u201d "
                "338 U. S., at 180 (dissenting opinion), continued:"
            ),
            _block("\u201cCourts can protect the innocent against such "
                   "invasions only indirectly.\u201d"),
        ])

        self.assertEqual([part.kind for part in parts],
                         ["majority", "dissent"])
        # A state court's title with no comma before the role still opens.
        parts = segment_blocks([
            _block("OPALA, Justice."),
            _block("The judgment is affirmed."),
            _block("KAUGER, Justice concurring in parts I and V."),
            _block("I join parts I and V."),
            _block("I write to say more."),
        ])
        self.assertEqual([part.kind for part in parts],
                         ["majority", "concurrence"])

    def test_a_line_up_after_the_opinion_opens_nothing(self):
        # In re 2016 Primary Election, 836 F.3d 584 (6th Cir. 2016): Scholar
        # sets the court's line-up after Sutton's opinion, which had been
        # read as the header, the line-up alone as the majority.
        parts = segment_blocks([
            Block(kind="center", spans=[Span(text="836 F.3d 584 (2016)")]),
            _block("Before: COLE, Chief Judge, and SILER and SUTTON, "
                   "Circuit Judges."),
            _block("SUTTON, Circuit Judge."),
            _block("A revealing difficulty with this matter is what to "
                   "name it."),
            _block("For these reasons, we vacate the district court's "
                   "preliminary injunction."),
            _block("SUTTON, J., delivered the opinion of the court in which "
                   "SILER, J., joined, and COLE, C.J., joined in part. COLE, "
                   "C.J., (pp. 8-10), delivered a separate opinion concurring "
                   "in part and dissenting in part."),
            _block("COLE, Chief Judge, concurring in part and dissenting in "
                   "part."),
            _block("Ohioans went to the polls on March 15, 2016."),
            _block("For these reasons, I concur in part and dissent in "
                   "part."),
        ])

        self.assertEqual([part.kind for part in parts],
                         ["header", "majority", "dissent"])
        self.assertEqual(parts[1].blocks[0].text(), "SUTTON, Circuit Judge.")

    def test_joined_byline_can_start_concurrence(self):
        parts = segment_blocks([
            _block("MR. JUSTICE SMITH delivered the opinion of the Court."),
            _block("The judgment is affirmed."),
            _block("JUSTICE JONES, with whom JUSTICE BROWN joins."),
            _block("I concur in the Court's judgment."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "concurrence"])

    def test_syllabus_announcement_is_not_a_separate_boundary(self):
        parts = segment_blocks([
            _block(
                "HARLAN, J., delivered the opinion of the Court. BLACKMUN, J., "
                "filed a dissenting opinion."
            ),
            _block("MR. JUSTICE HARLAN delivered the opinion of the Court."),
            _block("The judgment below is reversed."),
            _block(
                "MR. JUSTICE BLACKMUN, with whom THE CHIEF JUSTICE and "
                "MR. JUSTICE BLACK join."
            ),
            _block("I dissent, and I do so for two reasons:"),
        ])

        self.assertEqual(
            [part.kind for part in parts],
            ["header", "majority", "dissent"],
        )

    def test_vote_note_does_not_create_a_second_dissent(self):
        parts = segment_blocks([
            _block("MR. JUSTICE HARLAN delivered the opinion of the Court."),
            _block("The judgment below is reversed."),
            _block(
                "MR. JUSTICE BLACKMUN, with whom THE CHIEF JUSTICE and "
                "MR. JUSTICE BLACK join."
            ),
            _block("I dissent, and I do so for two reasons:"),
            _block(
                "MR. JUSTICE WHITE concurs in Paragraph 2 of MR. JUSTICE "
                "BLACKMUN'S dissenting opinion."
            ),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "dissent"])
        self.assertIn("WHITE concurs", parts[1].blocks[-1].text())


class TitleCommaBylineTests(unittest.TestCase):
    def test_stray_comma_after_justice_still_bounds_opinions(self):
        # Alleyne v. United States, 570 U.S. 99 (2013): Scholar's text
        # prints "Justice, BREYER," / "Chief Justice, ROBERTS," — a stray
        # comma between title and name that must not hide the bylines.
        parts = segment_blocks([
            _block("Justice THOMAS announced the judgment of the Court."),
            _block("Mandatory minimum sentences increase the penalty."),
            _block("Justice SOTOMAYOR, with whom Justice GINSBURG and "
                   "Justice KAGAN join, concurring."),
            _block("I join the opinion of the Court in full."),
            _block("Justice, BREYER, concurring in part and concurring in "
                   "the judgment."),
            _block("I cannot accept the majority's rationale."),
            _block("Chief Justice, ROBERTS, with whom Justice, SCALIA and "
                   "Justice, KENNEDY join, dissenting."),
            _block("I would adhere to our precedent."),
            _block("Justice, ALITO, dissenting."),
            _block("The Court overrules a well-entrenched case."),
        ])

        self.assertEqual(
            [part.kind for part in parts],
            ["majority", "concurrence", "concurrence", "dissent", "dissent"],
        )
        self.assertIn("BREYER", parts[2].label)
        self.assertIn("ROBERTS", parts[3].label)

    def test_comma_after_title_in_majority_attribution(self):
        # Armstrong v. Exceptional Child Ctr.: "Justice, SCALIA, delivered
        # the opinion of the Court, except as to Part IV."
        parts = segment_blocks([
            _block("Justice, SCALIA, delivered the opinion of the Court, "
                   "except as to Part IV."),
            _block("Medicaid is a federal-state program."),
            _block("Justice, SOTOMAYOR, with whom Justice KENNEDY joins, "
                   "dissenting."),
            _block("I respectfully dissent from the Court's holding."),
        ])

        self.assertEqual([part.kind for part in parts],
                         ["majority", "dissent"])


class SpelledOutRoleBylineTests(unittest.TestCase):
    def test_alabama_style_justice_bylines_start_separate_opinions(self):
        # Ex parte Murphy, 886 So. 2d 90 (Ala. 2003): the role is spelled
        # out after the name and the vote is a parenthetical — "LYONS,
        # Justice (dissenting)." — while the vote lines above it ("LYONS,
        # J., dissents.") are not boundaries.
        parts = segment_blocks([
            _block("STUART, Justice."),
            _block("James R. Murphy and Mary J. Murphy Benvenuto divorced."),
            _block("HOUSTON, SEE, BROWN, and WOODALL, JJ., concur."),
            _block(
                "HARWOOD, J., concurs in the rationale in part and concurs "
                "in the result."
            ),
            _block("LYONS, J., dissents."),
            _block(
                "HARWOOD, Justice (concurring in the rationale in part and "
                "concurring in the result)."
            ),
            _block("I concur in all aspects of the opinion except one."),
            _block("LYONS, Justice (dissenting)."),
            _block("I must respectfully dissent."),
        ])

        self.assertEqual(
            [part.kind for part in parts],
            ["majority", "concurrence", "dissent"],
        )
        self.assertIn("HARWOOD", parts[1].label)
        self.assertIn("LYONS", parts[2].label)
        # The vote lines stay with the majority opinion.
        self.assertIn("JJ., concur", parts[0].blocks[-3].text())


class GluedDispositionBylineTests(unittest.TestCase):
    def test_disposition_glued_to_byline_without_period(self):
        # King v. Burwell, 759 F.3d 358 (4th Cir. 2014): Scholar glues the
        # mandate to the concurrence byline with no period — "AFFIRMED
        # DAVIS, Senior Circuit Judge, concurring:".
        parts = segment_blocks([
            _block("GREGORY, Circuit Judge:"),
            _block("The plaintiffs challenge the IRS rule."),
            _block("AFFIRMED DAVIS, Senior Circuit Judge, concurring:"),
            _block("I write separately because the statute is clear."),
        ])

        self.assertEqual([p.kind for p in parts], ["majority", "concurrence"])
        self.assertTrue(
            parts[1].blocks[0].text().startswith("DAVIS"),
            parts[1].blocks[0].text(),
        )
        # The mandate stays with the majority opinion.
        self.assertIn("AFFIRMED", parts[0].blocks[-1].text())

    def test_disposition_blocks_do_not_become_phantom_parts(self):
        # Rothery Storage & Van Co. v. Atlas Van Lines, 792 F.2d 210 (D.C.
        # Cir. 1986): the disposition sentences precede the byline as their
        # own blocks; the 3-block joined window must not turn them into
        # phantom one-line concurrences.
        parts = segment_blocks([
            _block("BORK, Circuit Judge:"),
            _block("The antitrust claims fail as a matter of law."),
            _block("The judgment of the district court is"),
            _block("Affirmed."),
            _block("WALD, Circuit Judge, concurring:"),
            _block("I concur in the result and in much of the reasoning."),
        ])

        self.assertEqual([p.kind for p in parts], ["majority", "concurrence"])
        self.assertTrue(parts[1].blocks[0].text().startswith("WALD"))

    def test_so_ordered_glued_byline_splits(self):
        # Lorenzo v. SEC, 872 F.3d 578 (D.C. Cir. 2017): "So ordered.
        # KAVANAUGH, Circuit Judge, dissenting:" shares one block.
        parts = segment_blocks([
            _block("SRINIVASAN, Circuit Judge:"),
            _block("Substantial evidence supports the Commission."),
            _block("So ordered. KAVANAUGH, Circuit Judge, dissenting:"),
            _block("The Commission overreached in this case."),
        ])

        self.assertEqual([p.kind for p in parts], ["majority", "dissent"])
        self.assertTrue(parts[1].blocks[0].text().startswith("KAVANAUGH"))

    def test_page_break_running_head_is_not_a_byline(self):
        # Intel Corp. v. AMD, 542 U.S. 241 (2004): the facing-page running
        # head "BREYER, J., dissenting" rides in on a page marker with no
        # terminal punctuation; the majority's closing lines must not become
        # a phantom dissent.
        blocks = [
            _block("JUSTICE GINSBURG delivered the opinion of the Court."),
            _block("Section 1782 authorizes the discovery."),
            Block(kind="para", spans=[
                Span(text="*267 ", pagenum=True),
                Span(text="BREYER, J., dissenting"),
            ]),
            _block("For the reasons stated, the judgment is Affirmed."),
            _block("JUSTICE BREYER, dissenting."),
            _block("I cannot agree with the Court's reading."),
        ]
        parts = segment_blocks(blocks)

        self.assertEqual([p.kind for p in parts], ["majority", "dissent"])
        self.assertTrue(parts[1].blocks[0].text().startswith("JUSTICE BREYER"))


class VoteLineHeadingTests(unittest.TestCase):
    """The old reports note a justice's vote on a line of its own — "Mr.
    Justice DANIEL dissented." — and set the same line as the heading of the
    justice's opinion, when there is one: Mitchell v. Harmony, 54 U.S. 115
    (1852), had read as a majority opinion alone."""

    @staticmethod
    def _opinion(*tail):
        return [
            _block("54 U.S. 115 (1852)"),
            Block(kind="para", spans=[
                Span(text="*128", pagenum=True),
                Span(text=" Mr. Chief Justice TANEY delivered the opinion of "
                          "the court.")]),
            _block("The defendant took the plaintiff's property in Mexico."),
            _block("The judgment must be affirmed with costs."),
            *tail,
        ]

    def test_a_vote_heading_its_voters_opinion(self):
        parts = segment_blocks(self._opinion(
            Block(kind="para", spans=[
                Span(text="*138", pagenum=True),
                Span(text=" Mr. Justice DANIEL dissented.")]),
            _block("In this case I find myself constrained to disagree with "
                   "the opinion of the court just pronounced."),
            _block("The instructions took the facts from the jury."),
        ))
        self.assertEqual([p.kind for p in parts],
                         ["header", "majority", "dissent"])
        self.assertEqual(parts[2].label, "Mr. Justice DANIEL dissented.")
        self.assertEqual(len(parts[2].blocks), 3)

    def test_a_vote_with_no_opinion_of_its_own(self):
        for tail in (
                # The court's order follows (the Amistad, 40 U.S. 518).
                [_block("Mr. Justice BALDWIN dissented."),
                 _block("This cause came on to be heard on the transcript of "
                        "the record from the Circuit Court.")],
                # Two votes, their opinions headed apart (Dred Scott).
                [_block("Mr. Justice McLEAN and Mr. Justice CURTIS "
                        "dissented."),
                 _block("I am unable to agree with the court.")],
                # Another opinion's own byline.
                [_block("MR. JUSTICE RUTLEDGE dissents."),
                 _block("MR. JUSTICE MURPHY, dissenting."),
                 _block("I would reverse the judgment below.")],
                # A quotation the court sets out.
                [_block("Mr. Justice STORY dissented."),
                 _block("“I cannot concur,” he wrote, “in the judgment.”")]):
            with self.subTest(vote=tail[0].text()):
                kinds = [p.kind for p in segment_blocks(self._opinion(*tail))]
                self.assertNotIn(tail[0].text(), [
                    p.label for p in segment_blocks(self._opinion(*tail))])
                self.assertEqual(kinds.count("dissent"),
                                 1 if "MURPHY" in tail[1].text() else 0)

    def test_an_unsigned_opinion_and_a_vote_under_it(self):
        # State v. Ramos, 231 So. 3d 44 (La. Ct. App. 2017): no line names
        # the opinion's author, and the concurrence is headed by its vote.
        parts = segment_blocks([
            Block(kind="center", spans=[Span(text="STATE OF LOUISIANA v. "
                                                  "EVANGELISTO RAMOS")]),
            _block("The defendant appeals his conviction. " * 12),
            _block("For the above stated reasons, we affirm defendant's "
                   "conviction and sentence."),
            _block("AFFIRMED"),
            _block("LOBRANO, J., CONCURS IN THE RESULTS."),
            _block("I respectfully concur in the results of the majority "
                   "opinion."),
        ])
        self.assertEqual([p.kind for p in parts][-1], "concurrence")
        self.assertEqual(parts[-1].label,
                         "LOBRANO, J., CONCURS IN THE RESULTS.")


class HistoricalAndSignatureBoundaryTests(unittest.TestCase):
    def test_myers_definite_article_separate_opinion_heading(self):
        # Myers v. United States, 272 U.S. 52, 178 (1926): Scholar includes
        # both a page marker and a definite article in McReynolds's neutral
        # heading.  Neither should hide the separate-opinion boundary.
        parts = segment_blocks([
            _block("MR. CHIEF JUSTICE TAFT delivered the opinion of the Court."),
            _block("Judgment affirmed."),
            _block("MR. JUSTICE HOLMES, dissenting."),
            _block("I agree with the conclusions of my colleagues."),
            Block(kind="para", spans=[
                Span(text="*178", pagenum=True),
                Span(
                    text=(
                        " The separate opinion of MR. JUSTICE McREYNOLDS."
                    )
                ),
            ]),
            _block("The statute has not been repealed or superseded."),
            _block("The constitutional question must therefore be resolved."),
            _block("MR. JUSTICE BRANDEIS, dissenting."),
            _block("The issue is whether the removal was lawful."),
        ])

        self.assertEqual(
            [part.kind for part in parts],
            ["majority", "dissent", "separate", "dissent"],
        )
        self.assertIn("McREYNOLDS", parts[2].label)
        self.assertNotIn("*178", parts[2].label)
        self.assertTrue(parts[2].blocks[0].spans[0].pagenum)

    def test_unresolved_roleless_opinion_remains_neutral(self):
        parts = segment_blocks([
            _block("MR. JUSTICE MARSHALL delivered the opinion of the Court."),
            _block("The Court resolves the question presented."),
            _block("Separate opinion of MR. JUSTICE STORY."),
            _block("The historical materials point in several directions."),
            _block("The statutory language supplies another consideration."),
            _block("That is sufficient to resolve the issue before us."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "separate"])

    def test_a_curly_apostrophe_in_the_justice_s_name_still_heads_a_part(self):
        # The heading pattern once held the curly apostrophe mis-encoded
        # (UTF-8 read as cp1252), so a name printed with one never started a
        # part.
        parts = segment_blocks([
            _block("MR. JUSTICE MARSHALL delivered the opinion of the Court."),
            _block("The Court resolves the question presented."),
            _block("Separate opinion of MR. JUSTICE O’CONNOR."),
            _block("The historical materials point in several directions."),
            _block("The statutory language supplies another consideration."),
            _block("That is sufficient to resolve the issue before us."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "separate"])
        self.assertIn("O’CONNOR", parts[1].label)

    def test_long_roleless_opinion_uses_explicit_closing_dissent(self):
        # Osborn v. Bank of the United States, 22 U.S. (9 Wheat.) 738
        # (1824): Johnson's neutral heading does not identify his vote; his
        # conclusion does.
        parts = segment_blocks([
            _block("Mr. Chief Justice MARSHALL delivered the opinion of the Court."),
            _block("The decree of the Circuit Court is affirmed."),
            _block("Mr. Justice JOHNSON."),
            _block("The argument in this cause presents three questions."),
            _block("The first question concerns the statutory grant."),
            _block("The second concerns the constitutional power."),
            _block("The final question concerns the exercise of jurisdiction."),
            _block(
                "Upon the whole, I feel compelled to dissent from the Court, "
                "on the point of jurisdiction."
            ),
            _block("Decree affirmed."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "dissent"])
        self.assertIn("JOHNSON", parts[1].label.upper())

    def test_roleless_separate_opinion_of_headings_use_disposition_structure(self):
        # Steward Machine Co. v. Davis, 301 U.S. 548 (1937), gives
        # McReynolds and Sutherland noun-first headings with no role.  The
        # Court affirms; Sutherland would reverse and says he concurs with
        # McReynolds's immediately preceding writing.
        parts = segment_blocks([
            _block("MR. JUSTICE CARDOZO delivered the opinion of the Court."),
            _block("The judgment is"),
            _block("Affirmed."),
            _block("Separate opinion of MR. JUSTICE McREYNOLDS."),
            _block("The legislation, I think, exceeds the power of Congress."),
            _block("The federal plan imperils the independence of the States."),
            _block("Separate opinion of MR. JUSTICE SUTHERLAND."),
            _block(
                "With most of what is said in the opinion just handed down, "
                "I concur."
            ),
            _block("The administrative provisions invade reserved powers."),
            _block(
                "For the foregoing reasons, I think the judgment below "
                "should be reversed."
            ),
            _block("MR. JUSTICE BUTLER, dissenting."),
            _block("The objections in both separate opinions are well taken."),
        ])

        self.assertEqual(
            [part.kind for part in parts],
            ["majority", "dissent", "dissent", "dissent"],
        )
        self.assertIn("McREYNOLDS", parts[1].label)
        self.assertIn("SUTHERLAND", parts[2].label)

    def test_chief_justice_old_style_byline_opens_majority(self):
        # Fletcher v. Peck, 10 U.S. (6 Cranch) 87 (1810), uses the older
        # "Ch. J." title and gives Johnson's disagreement a bare byline.
        parts = segment_blocks([
            _block("Marshall, Ch. J."),
            _block("The question whether the legislature could repeal the act remains."),
            _block("Johnson, J."),
            _block(
                "In this case I entertain an opinion different from that "
                "which has been delivered by the court."
            ),
            _block("I therefore rest my opinion on another ground."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "dissent"])
        self.assertTrue(parts[0].blocks[0].text().startswith("Marshall"))
        self.assertTrue(parts[1].blocks[0].text().startswith("Johnson"))

    def test_repeated_court_opinion_is_not_called_a_concurrence(self):
        parts = segment_blocks([
            _block("Marshall, Ch. J."),
            _block("delivered the opinion of the court upon the pleadings, as follows:"),
            _block("The first issue is resolved."),
            _block("Marshall, Ch. J."),
            _block("delivered the opinion of the court as follows:"),
            _block("The amended pleadings present a second issue."),
            _block("Johnson, J."),
            _block("I entertain an opinion different from that delivered by the court."),
        ])

        self.assertEqual(
            [part.kind for part in parts], ["majority", "majority", "dissent"]
        )

    def test_ocr_seriatim_justice_bylines_remain_separate(self):
        parts = segment_blocks([
            _block("The Court,"),
            _block("delivered their opinions, feriatim, as follow:"),
            _block("Chace, Jujtice."),
            _block("I consider the first question."),
            _block("This writing continues for several paragraphs."),
            _block("Paterson, Juftke."),
            _block("My opinion rests on the treaty."),
            _block("This writing also continues."),
            _block("R~DELL,’7ujh~?~. *"),
            _block("I take a different route through the treaty."),
            _block("This seriatim writing continues."),
            _block("Wilson, JuJiice."),
            _block("I shall be concise in delivering my opinion."),
            _block("The judgment should be reversed."),
        ])

        self.assertEqual(
            [part.kind for part in parts],
            ["header", "majority", "majority", "majority", "majority"],
        )

    def test_vote_signatures_do_not_create_phantom_concurrences(self):
        # State v. Gregory, 427 P.3d 621 (Wash. 2018), ends the lead writing
        # and the true concurrence with several one-line vote signatures.
        parts = segment_blocks([
            _block("FAIRHURST, C.J."),
            _block("We hold the death penalty is unconstitutional as administered."),
            _block("WIGGINS, J."),
            _block("YU, J."),
            _block("JOHNSON, J."),
            _block("I concur in the result reached by the majority."),
            _block("The record supplies an additional ground."),
            _block("OWENS, J."),
            _block("MADSEN, J."),
            _block("STEPHENS, J."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "concurrence"])
        self.assertIn("JOHNSON", parts[1].label)
        self.assertIn("STEPHENS, J.", parts[1].blocks[-1].text())

    def test_public_domain_paragraph_number_does_not_hide_byline(self):
        parts = segment_blocks([
            _block("¶1 ANN WALSH BRADLEY, J. The question presented is narrow."),
            _block("¶2 We affirm the court of appeals."),
            _block(
                "¶73 PATIENCE DRAKE ROGGENSACK, J. (concurring). Although I "
                "agree with the mandate, I write separately."
            ),
            _block("¶74 The text supplies a different rationale."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "concurrence"])
        self.assertIn("ROGGENSACK", parts[1].label)

    def test_filing_banner_can_share_dissent_byline(self):
        parts = segment_blocks([
            _block("The emergency motion for a stay is granted."),
            _block("The order will remain in effect pending appeal."),
            _block(
                "6 FILED Duncan v. Bonta OCT 10 2023 MOLLY C. DWYER, CLERK "
                "R. NELSON, Circuit Judge, dissenting: U.S. COURT OF APPEALS"
            ),
            _block("I join Judge Bumatay's dissent."),
            _block(
                "2 FILED OCT 10 2023 MOLLY C. DWYER, CLERK BUMATAY, Circuit "
                "Judge, joined by IKUTA, R. NELSON, and"
            ),
            _block("VANDYKE, Circuit Judges, dissenting:"),
            _block("The majority applies the wrong standard."),
        ])

        self.assertEqual([part.kind for part in parts], ["majority", "dissent", "dissent"])


    def test_a_list_of_the_writings_opens_none_of_them(self):
        # PHH Corp. v. CFPB, 881 F.3d 75 (D.C. Cir. 2018) (en banc): the
        # court's list of who filed what precedes the separate opinions, and
        # "filed" in it was read as a clerk's filing banner — two phantom
        # opinions, labelled "filed by Senior Circuit Judge Randolph. Tatel".
        parts = segment_blocks([
            _block("PILLARD, Circuit Judge:"),
            _block("We granted en banc review to consider the statute."),
            _block("Concurring opinion filed by Circuit Judge Tatel, with "
                   "whom Circuit Judges Millett and Pillard join."),
            _block("Dissenting opinion filed by Circuit Judge Kavanaugh, "
                   "with whom Senior Circuit Judge Randolph joins."),
            _block("Dissenting opinion filed by Senior Circuit Judge "
                   "Randolph."),
            _block("Tatel, Circuit Judge, with whom Circuit Judges Millett "
                   "and Pillard join, concurring:"),
            _block("Finding no way to avoid the constitutional question, I "
                   "concur."),
            _block("Randolph, Senior Circuit Judge, dissenting:"),
            _block("The Director is an officer of the United States."),
        ])
        self.assertEqual([part.kind for part in parts],
                         ["majority", "concurrence", "dissent"])
        self.assertTrue(parts[1].label.startswith("Tatel"))


class EnBancOrderTests(unittest.TestCase):
    """Acad. of Allergy & Asthma in Primary Care v. Amerigroup Tenn., Inc.,
    164 F.4th 529 (6th Cir. 2026): the order denying rehearing en banc, a
    statement respecting the denial and a concurrence in it, each under the
    heading the Sixth Circuit sets between rules, each byline running on
    into the writing's first sentence."""

    BLOCKS = [
        "UNITED STATES COURT OF APPEALS FOR THE SIXTH CIRCUIT",
        "On Petition for Rehearing En Banc United States District Court for "
        "the Eastern District of Tennessee at Knoxville.",
        "Decided and Filed: January 13, 2026",
        "Before: SUTTON, Chief Judge; KETHLEDGE and MURPHY, Circuit Judges.",
        "_________________",
        "COUNSEL",
        "ON PETITION FOR REHEARING EN BANC and REPLY: Casey Low, PILLSBURY "
        "WINTHROP SHAW PITTMAN LLP, Austin, Texas, for Appellant.",
        "The court delivered an ORDER denying the petition for rehearing en "
        "banc. BUSH, J. (pp. 3–18), delivered a statement respecting the "
        "denial of the petition for rehearing en banc. MURPHY, J. (pp. "
        "19–24), also delivered a separate opinion, in which SUTTON, C.J. "
        "and KETHLEDGE, J., joined.",
        "_________________",
        "ORDER _________________",
        "The court received a petition for rehearing en banc. The original "
        "panel has reviewed the petition for rehearing.",
        "The petition was then circulated to the full court. No judge "
        "requested a vote on the suggestion for rehearing en banc.",
        "Therefore, the petition is denied.",
        "__________________",
        "STATEMENT __________________",
        "JOHN K. BUSH, Circuit Judge, respecting the denial of rehearing en "
        "banc. This case involves an alleged conspiracy to restrain trade.",
        "I respect the panel’s decision, though I would not concur in all of "
        "it. But I write separately to suggest that this case may warrant "
        "the Court’s review.",
        "__________________",
        "CONCURRENCE __________________",
        "MURPHY, Circuit Judge, with whom SUTTON, Chief Judge, and "
        "KETHLEDGE, Circuit Judge, join, concurring in the denial of "
        "rehearing en banc. Congress has granted a cause of action.",
        "In his statement respecting the denial of rehearing en banc, Judge "
        "Bush finds this holding debatable on four fronts.",
        "First, Judge Bush asserts that Illinois Brick’s rule should apply "
        "only to a plaintiff who seeks an illegal overcharge.",
    ]

    def test_the_order_the_statement_and_the_concurrence(self):
        parts = segment_blocks([_block(t) for t in self.BLOCKS])
        self.assertEqual([p.kind for p in parts],
                         ["header", "majority", "separate", "concurrence"])
        header, order, statement, concurrence = parts
        # The bench, counsel and line-up are the order's front matter.
        self.assertEqual(header.blocks[-1].text(), self.BLOCKS[7])
        self.assertEqual(order.label, "Order")
        self.assertEqual([b.text() for b in order.blocks[:2]],
                         ["_________________", "ORDER _________________"])
        # A statement neither concurs nor dissents, whatever it says of
        # concurring.
        self.assertEqual(
            statement.label,
            "JOHN K. BUSH, Circuit Judge, respecting the denial of rehearing "
            "en banc.")
        self.assertEqual(statement.blocks[1].text(),
                         "STATEMENT __________________")
        # The byline stands apart from the sentence it ran on into.
        self.assertEqual(statement.blocks[2].text(), statement.label)
        self.assertEqual(statement.blocks[3].text(),
                         "This case involves an alleged conspiracy to "
                         "restrain trade.")
        self.assertTrue(concurrence.label.startswith(
            "MURPHY, Circuit Judge, with whom SUTTON"))
        self.assertEqual(concurrence.blocks[1].text(),
                         "CONCURRENCE __________________")
        self.assertEqual(concurrence.blocks[3].text(),
                         "Congress has granted a cause of action.")
        # "…, Judge Bush finds" names a judge in a sentence: it opens nothing.
        self.assertIn(self.BLOCKS[-2], [b.text() for b in concurrence.blocks])

    def test_a_panel_opinion_s_run_on_concurrence(self):
        # The panel's opinion, 155 F.4th 795 (6th Cir. 2025): Judge
        # Kethledge's concurrence had stayed in the majority.
        parts = segment_blocks([_block(t) for t in [
            "Before: SUTTON, Chief Judge; KETHLEDGE and MURPHY, Circuit "
            "Judges. _________________",
            "MURPHY, J., delivered the opinion of the court in which SUTTON, "
            "C.J., and KETHLEDGE, J., concurred. KETHLEDGE, J. (pp. 42–43), "
            "delivered a separate concurring opinion. _________________",
            "OPINION _________________",
            "MURPHY, Circuit Judge. The plaintiff in this case provides "
            "personnel and supplies to primary-care physicians.",
            "We affirm.",
            "__________________",
            "CONCURRENCE __________________",
            "KETHLEDGE, Circuit Judge, concurring. I join the court’s "
            "opinion, which offers a careful synthesis of the caselaw.",
            "The goal of antitrust law is to advance consumer welfare.",
        ]])
        self.assertEqual([p.kind for p in parts][-2:],
                         ["majority", "concurrence"])
        self.assertEqual(parts[-1].label,
                         "KETHLEDGE, Circuit Judge, concurring.")
        self.assertEqual(parts[-2].blocks[-1].text(), "We affirm.")
        self.assertEqual(parts[-1].blocks[3].text(),
                         "I join the court’s opinion, which offers a careful "
                         "synthesis of the caselaw.")

    def test_the_supreme_court_s_statement_respecting_a_denial(self):
        parts = segment_blocks([_block(t) for t in [
            "SUPREME COURT OF THE UNITED STATES",
            "PER CURIAM.",
            "The petition for a writ of certiorari is denied.",
            "Statement of JUSTICE SOTOMAYOR respecting the denial of "
            "certiorari.",
            "I concur in the denial, but write to note a troubling "
            "question that the petition raises.",
            "JUSTICE THOMAS, dissenting from the denial of certiorari.",
            "I would grant the petition.",
        ]])
        self.assertEqual([p.kind for p in parts][-2:],
                         ["separate", "dissent"])
        self.assertTrue(parts[-2].label.startswith("Statement of JUSTICE "
                                                   "SOTOMAYOR"))

    def test_a_run_on_byline_ending_in_a_colon_stays_whole(self):
        # The patterns that already read "X, Circuit Judge, concurring:
        # text" keep it in one block.
        line = ("WALD, Circuit Judge, concurring: I write separately to "
                "add a word about the remedy.")
        parts = segment_blocks([_block(t) for t in [
            "PER CURIAM:", "The judgment is affirmed.", line, "More."]])
        self.assertEqual(parts[-1].kind, "concurrence")
        self.assertEqual(parts[-1].blocks[0].text(), line)


if __name__ == "__main__":
    unittest.main()
