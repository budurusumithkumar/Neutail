"""Stable parsing and ranking policy constants for product discovery."""

RANKING_WEIGHTS = {
    "occasion": 0.25,
    "style": 0.15,
    "color": 0.10,
    "segment": 0.20,
    "price": 0.10,
    "semantic": 0.15,
    "availability": 0.05,
}

CATEGORY_TERMS = {
    "accessories": "Accessories",
    "accessory": "Accessories",
    "bottoms": "Bottoms",
    "coats": "Outerwear",
    "coat": "Outerwear",
    "dresses": "Dresses",
    "dress": "Dresses",
    "footwear": "Footwear",
    "jackets": "Outerwear",
    "jacket": "Outerwear",
    "knitwear": "Knitwear",
    "outerwear": "Outerwear",
    "shirts": "Shirts",
    "shirt": "Shirts",
    "shoes": "Footwear",
    "shoe": "Footwear",
    "tops": "Tops",
    "top": "Tops",
    "trousers": "Trousers",
}

SUBCATEGORY_TERMS = {
    "maxi dress": "Maxi Dress",
    "midi dress": "Midi Dress",
    "mini dress": "Mini Dress",
    "formal shoes": "Formal Shoes",
    "casual shirt": "Casual Shirt",
    "t-shirt": "T-Shirt",
    "knit top": "Knit Top",
}

COLOR_TERMS = (
    "black",
    "blue",
    "burgundy",
    "cream",
    "beige",
    "green",
    "grey",
    "navy",
    "purple",
    "red",
    "white",
)

STYLE_TERMS = (
    "classic",
    "contemporary",
    "minimal",
    "modern",
    "relaxed",
    "romantic",
    "statement",
    "tailored",
)

OCCASION_TERMS = {
    "everyday": "Everyday",
    "formal": "Formal",
    "party": "Party",
    "smart casual": "Smart Casual",
    "travel": "Travel",
    "wedding": "Wedding",
    "weekend": "Weekend",
    "work": "Work",
}

SEMANTIC_TERMS = frozenset(
    {
        "affordable",
        "chic",
        "comfortable",
        "elegant",
        "evening",
        "luxurious",
        "luxury",
        "sophisticated",
        "special",
        "stylish",
        "trendy",
        "vibe",
    }
)

SIMILAR_ITEM_TERMS = (
    "alternatives",
    "like this",
    "more like",
    "similar",
)

EDITORIAL_STYLES = frozenset(
    {"classic", "contemporary", "statement", "tailored"}
)

__all__ = [
    "CATEGORY_TERMS",
    "COLOR_TERMS",
    "EDITORIAL_STYLES",
    "OCCASION_TERMS",
    "RANKING_WEIGHTS",
    "SEMANTIC_TERMS",
    "SIMILAR_ITEM_TERMS",
    "STYLE_TERMS",
    "SUBCATEGORY_TERMS",
]
