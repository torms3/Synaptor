module SemanticMap


"""

    make_semantic_assignment( seg, sem_weight, classes )

  Takes a segmentation, a voxelwise weight map over several classes,
  and indices of the possible assignment classes. Assigns each segment in the
  segmentation (aside from 0) to one of the assignment classes by comparing
  the sum probability over each voxel for each candidate class.
"""
function make_semantic_assignment{sT,wT}( seg::Array{sT}, sem_weight::Array{wT},
                                      classes::Vector{Int},
                                      weights::Dict{sT,Vector{wT}}=Dict{sT,Vector{wT}}() )

  segids = Set(unique(seg))
  delete!(segids, eltype(segids)(0))

  num_classes = length(classes)
  for segid in segids
    if !haskey(weights,segid)  weights[segid] = zeros(wT,(num_classes,)) end
  end

  sx,sy,sz = size(seg)
  sTzero = sT(0)
  for (i,cl) in enumerate(classes), z in 1:sz, y in 1:sy, x in 1:sx

    segid = seg[x,y,z];
    if segid == sTzero  continue  end

    weights[segid][i] += sem_weight[x,y,z,cl];

  end

  make_assignment(weights, classes), weights
end


"""

    function addsemweight{sT,wT}( semweights::Dict{sT,Vector{wT}}... )

  Returns a new Dict with each entry equal to the sum of the entries
  under a particular key of the constituent semantic maps.
"""
function add_semmaps{sT,wT}( semweights::Dict{sT,Vector{wT}}... )

  all_keys = union([keys(sw) for sw in semweights]...)
  first_val = first(values(semweights[1]))

  sumweights = Dict( k => zeros(wT, length(first_val)) for k in all_keys )

  for sw in semweights, (k,v) in sw
    sumweights[k] += v
  end

  sumweights
end


"""

    make_assignment{sT,wT}( semweight{sT,Vector{wT}}, classes=1:N )

  Assign segments to their max-weight category given the weight dict
"""
function make_assignment{sT,wT}( semweights::Dict{sT,Vector{wT}}, classes )
  Dict{sT,Int}( segid => classes[ findmax(semweights[segid])[2] ]
                for segid in keys(semweights) )
end


function make_assignment{sT,wT}( semweights::Dict{sT,Vector{wT}} )
  Dict{sT,Int}( segid => findmax(semweights[segid])[2]
               for segid in keys(semweights) )
end


#=================================================================
=================================================================#

"""
Old implementation of make_semantic_assignment
"""
function old_impl{T}( seg::Array{T}, sem_probs, classes::Array{Int},
  weights::Dict{T,Vector{Float64}}=Dict{T,Vector{Float64}}() )

  class_index = Dict{Int,Int}( classes[i] => i for i in eachindex(classes) );

  #segid => array : weight of each class
  #if weights == nothing weights = Dict{Int,Vector{Float64}}() end
  num_classes = length(classes);

  #accumulating weight for each segment
  for k in classes
    class_k = class_index[k]
    for z in 1:size(seg,3)
      for y in 1:size(seg,2)
        for x in 1:size(seg,1)

          segid = seg[x,y,z];
          if segid == eltype(seg)(0) continue end

          if !haskey(weights, segid)  weights[ segid ] = zeros((num_classes,)) end

          weights[ segid ][ class_k ] += sem_probs[x,y,z,k];

        end
      end
    end
  end

  #making assignments by max weight
  #index 2 selects index of max (instead of value)
  Dict{T,Int}( k => classes[ findmax(weights[k])[2] ] for k in keys(weights) ), weights
end

end #module SemanticMap