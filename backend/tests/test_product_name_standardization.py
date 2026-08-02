from app.operations import product_name_similarity, standardized_product_display_name


def test_dash_spacing_and_package_word_variants_match():
    left="Aashirvaad Atta 5 kg -- Packed 5 kg"
    right="Aashirvaad Atta 5 kg — Pack 5 kg"
    assert product_name_similarity(left,right)==1


def test_small_brand_spelling_error_matches():
    assert product_name_similarity("Ashirvaad Atta 5kg","Aashirvaad Atta 5 kg")>=0.84


def test_different_pack_sizes_never_match():
    assert product_name_similarity("India Gate Rice Packed 1 kg","India Gate Rice — Packed 5 kg")==0


def test_loose_and_packed_never_match():
    assert product_name_similarity("Sugar — Loose","Sugar — Packed 1 kg")==0


def test_display_name_normalizes_units_and_separators():
    assert standardized_product_display_name("  Tata Salt  --  Packed 1 kilograms ")=="Tata Salt — Packed 1 kg"
