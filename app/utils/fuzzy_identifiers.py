"""Bounded edit distance for suggestions; never a substitute for exact evidence."""
def distance(left,right,budget=1):
    if abs(len(left)-len(right))>budget: return budget+1
    previous=list(range(len(right)+1))
    for i,a in enumerate(left,1):
        current=[i]
        for j,b in enumerate(right,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        if min(current)>budget: return budget+1
        previous=current
    return previous[-1]


def near_identifiers(requested,candidates):
    matches=[]
    for original in sorted(requested):
        if not 6<=len(original)<=128: continue
        budget=1 if len(original)<16 else 2
        for candidate in sorted(candidates):
            if len(candidate)>128: continue
            edits=distance(original,candidate,budget)
            if 0<edits<=budget:
                matches.append({'requested':original,'candidate':candidate,'distance':edits})
    return sorted(matches,key=lambda m:(m['distance'],m['candidate']))
